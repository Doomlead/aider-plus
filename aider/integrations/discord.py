"""Discord front-end helpers for running Aider in headless scripting mode.

This module keeps discord.py optional so core aider installs do not require it.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional, Set

from aider.agent import AiderAgentLoop
from aider.agent.loop import AgentLoopConfig
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.product import ProductDepartment
from aider.company.schemas import CompanyTask
from aider.coders import Coder
from aider.main import main as aider_main
from aider.memory import ConversationMemory, ProjectMemory, consolidate_conversation


@dataclass(frozen=True)
class DiscordSessionKey:
    guild_id: int
    channel_id: int
    user_id: Optional[int] = None
    repo_path: Optional[str] = None


@dataclass
class RepositoryPolicy:
    allowed_roots: Set[str] = field(default_factory=set)

    def validate(self, repo_path: str):
        resolved = str(Path(repo_path).resolve())
        if not self.allowed_roots:
            return
        for root in self.allowed_roots:
            root_resolved = str(Path(root).resolve())
            if resolved == root_resolved or resolved.startswith(root_resolved + "/"):
                return
        raise PermissionError(f"Repository path is not in the whitelist: {repo_path}")


@dataclass
class DiscordAiderConfig:
    max_runtime_seconds: int = 300
    max_prompt_chars: int = 12_000
    auto_commit: bool = False
    dry_run: bool = False
    allow_users: Set[int] = field(default_factory=set)
    deny_users: Set[int] = field(default_factory=set)
    repository_policy: RepositoryPolicy = field(default_factory=RepositoryPolicy)
    use_architect_mode: bool = True
    architect_model: Optional[str] = None
    editor_model: Optional[str] = None


class DiscordSessionManager:
    """In-memory session store keyed by channel/user/repo for easy future persistence."""

    def __init__(self):
        self._sessions: Dict[DiscordSessionKey, Coder] = {}
        self._last_used: Dict[DiscordSessionKey, float] = {}
        self._project_memories: Dict[DiscordSessionKey, ProjectMemory] = {}

    def get(self, key: DiscordSessionKey) -> Optional[Coder]:
        coder = self._sessions.get(key)
        if coder:
            self._last_used[key] = time.time()
        return coder

    def put(self, key: DiscordSessionKey, coder: Coder):
        self._sessions[key] = coder
        self._last_used[key] = time.time()

    def remove(self, key: DiscordSessionKey):
        self.persist_project_memory(key)
        self._sessions.pop(key, None)
        self._last_used.pop(key, None)

    def attach_project_memory(self, key: DiscordSessionKey, project_memory: ProjectMemory):
        self._project_memories[key] = project_memory

    def persist_project_memory(self, key: DiscordSessionKey):
        project_memory = self._project_memories.get(key)
        coder = self._sessions.get(key)
        if project_memory and coder:
            conversation_memory = getattr(coder, "conversation_memory", None)
            if isinstance(conversation_memory, ConversationMemory):
                consolidate_conversation(conversation_memory, project_memory)
        if project_memory:
            project_memory.persist()

    def list_keys(self):
        return list(self._sessions.keys())


class DiscordAiderBot:
    """Async-friendly façade around Coder for Discord handlers.

    You can wire `run_instruction` into slash commands or mention handlers.
    """

    def __init__(self, config: Optional[DiscordAiderConfig] = None):
        self.config = config or DiscordAiderConfig()
        self.sessions = DiscordSessionManager()
        self.orchestrator: Optional[CompanyOrchestrator] = None
        self.engineering: Optional[EngineeringDepartment] = None
        self.product: Optional[ProductDepartment] = None
        self.active_project = None

    def check_access(self, user_id: int):
        if user_id in self.config.deny_users:
            raise PermissionError("User is blocked from running aider")
        if self.config.allow_users and user_id not in self.config.allow_users:
            raise PermissionError("User is not in the allowed user list")

    def _build_coder(self, repo_path: str, model: Optional[str] = None) -> Coder:
        argv = [
            "--headless",
            "--yes-always",
            "--no-auto-commits" if not self.config.auto_commit else "--auto-commits",
            "--dry-run" if self.config.dry_run else "--no-dry-run",
        ]
        if model:
            argv.extend(["--model", model])
        argv.append(repo_path)

        coder = aider_main(argv=argv, return_coder=True)
        if not isinstance(coder, Coder):
            raise RuntimeError("Unable to create aider coder for Discord session")
        return coder

    async def get_or_create_session(
        self,
        key: DiscordSessionKey,
        repo_path: str,
        model: Optional[str] = None,
    ) -> Coder:
        self.config.repository_policy.validate(repo_path)
        existing = self.sessions.get(key)
        if existing:
            project_memory = getattr(existing, "project_memory", None)
            if isinstance(project_memory, ProjectMemory):
                project_memory.load()
            return existing

        coder = await asyncio.to_thread(self._build_coder, repo_path, model)
        coder.conversation_memory = ConversationMemory()
        project_memory = ProjectMemory(repo_path)
        project_memory.load()
        coder.project_memory = project_memory
        self.sessions.attach_project_memory(key, project_memory)
        self.sessions.put(key, coder)
        return coder

    def _init_company_session(
        self,
        coder: Coder,
        callback: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    ) -> EngineeringDepartment:
        agent_loop = AiderAgentLoop(
            coder=coder,
            callback=callback,
            config=AgentLoopConfig(
                use_architect_mode=self.config.use_architect_mode,
                architect_model=self.config.architect_model,
                editor_model=self.config.editor_model,
            ),
        )
        self.engineering = EngineeringDepartment(
            project_memory=coder.project_memory,
            agent_loop=agent_loop,
            conversation_memory=coder.conversation_memory,
        )
        self.product = ProductDepartment(
            project_memory=coder.project_memory,
            conversation_memory=None,
        )
        self.orchestrator = CompanyOrchestrator(project_memory=coder.project_memory)
        self.orchestrator.register(self.product)
        self.orchestrator.register(self.engineering)
        return self.engineering

    async def receive_human_input(
        self,
        *,
        key: DiscordSessionKey,
        repo_path: str,
        user_id: int,
        prompt: str,
        model: Optional[str] = None,
        callback: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    ):
        """Unified human entry point: bootstrap via Product, then iterate in Engineering."""
        if not self.active_project:
            return await self.run_prototype(
                key=key,
                repo_path=repo_path,
                user_id=user_id,
                prompt=prompt,
                model=model,
                callback=callback,
            )

        return await self.run_instruction(
            key=key,
            repo_path=repo_path,
            user_id=user_id,
            prompt=prompt,
            model=model,
            callback=callback,
        )

    async def run_prototype(
        self,
        *,
        key: DiscordSessionKey,
        repo_path: str,
        user_id: int,
        prompt: str,
        model: Optional[str] = None,
        callback: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    ):
        """Start a new project by routing the prompt through Product before Engineering."""
        self.check_access(user_id)
        if len(prompt) > self.config.max_prompt_chars:
            raise ValueError("Prompt too large")

        coder = await self.get_or_create_session(key, repo_path, model=model)
        self.on_reconnect_or_ping(key)
        self._init_company_session(coder, callback=callback)

        task = CompanyTask(
            task_id=str(uuid.uuid4()),
            origin="ceo",
            target="product",
            artifact_type="raw_prompt",
            payload=prompt,
            blocking=False,
        )

        deliverable = await self.product.process(task)
        if self.orchestrator:
            await self.orchestrator._route(deliverable)

        return {
            "task_id": task.task_id,
            "summary": deliverable.payload,
            "content": deliverable.payload,
            "artifact_type": deliverable.artifact_type,
            "status": deliverable.status,
            "metadata": deliverable.metadata,
        }

    async def run_instruction(
        self,
        *,
        key: DiscordSessionKey,
        repo_path: str,
        user_id: int,
        prompt: str,
        model: Optional[str] = None,
        include_diff: bool = False,
        callback: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    ):
        """Run an existing-project instruction directly in Engineering without PRD generation."""
        self.check_access(user_id)
        if len(prompt) > self.config.max_prompt_chars:
            raise ValueError("Prompt too large")

        coder = await self.get_or_create_session(key, repo_path, model=model)
        self.on_reconnect_or_ping(key)

        engineering = self._init_company_session(coder, callback=callback)
        task = CompanyTask(
            task_id=str(uuid.uuid4()),
            origin="ceo",
            target="engineering",
            artifact_type="raw_prompt",
            payload=prompt,
            blocking=False,
        )

        run_task = asyncio.create_task(self._run_engineering_task(engineering, task))

        try:
            deliverable = await asyncio.wait_for(run_task, timeout=self.config.max_runtime_seconds)
        except asyncio.TimeoutError as err:
            raise TimeoutError("Aider request timed out") from err

        result_content = deliverable.payload
        files = deliverable.metadata.get("files", [])
        commits = deliverable.metadata.get("commits", [])
        diffs = deliverable.metadata.get("diffs", [])
        result = {
            "summary": result_content,
            "content": result_content,
            "files_changed": files,
            "files": files,
            "commits": commits,
            "diffs": diffs,
            "status": deliverable.status,
        }

        project_memory = getattr(coder, "project_memory", None)
        if isinstance(project_memory, ProjectMemory):
            project_memory.update({"last_prompt": prompt, "last_result": result_content})

        return result

    async def _run_engineering_task(
        self,
        engineering: EngineeringDepartment,
        task: CompanyTask,
    ):
        deliverable = await engineering.process(task)
        if self.orchestrator:
            for handler in self.orchestrator._handlers:
                try:
                    await handler(deliverable)
                except Exception:
                    pass
        return deliverable

    def on_disconnect(self, key: DiscordSessionKey):
        """Persist project memory when a Discord session disconnects."""
        self.sessions.persist_project_memory(key)

    def on_reconnect_or_ping(self, key: DiscordSessionKey):
        """Refresh project memory so new runs receive persisted context."""
        coder = self.sessions.get(key)
        if not coder:
            return
        project_memory = getattr(coder, "project_memory", None)
        if isinstance(project_memory, ProjectMemory):
            project_memory.load()


def build_discord_client(*args, **kwargs):
    """Factory that imports discord.py lazily.

    This keeps aider importable without discord.py installed.
    """

    try:
        import discord
        from discord.ext import commands
    except ImportError as err:
        raise ImportError("Install discord.py to use Discord integrations") from err

    aider_bot = kwargs.pop("aider_bot", None)
    repo_path_resolver = kwargs.pop("repo_path_resolver", None)
    model_resolver = kwargs.pop("model_resolver", None)

    intents = kwargs.pop("intents", None)
    if intents is None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.message_content = True

    bot = commands.Bot(*args, intents=intents, **kwargs)

    if aider_bot is not None:

        @bot.command(name="prototype")
        async def prototype(ctx, *, prompt: str):
            repo_path = (
                repo_path_resolver(ctx) if repo_path_resolver else getattr(ctx, "repo_path", None)
            )
            if not repo_path:
                await ctx.send("Repository path is required for /prototype.")
                return

            model = model_resolver(ctx) if model_resolver else None
            key = DiscordSessionKey(
                guild_id=getattr(getattr(ctx, "guild", None), "id", 0) or 0,
                channel_id=ctx.channel.id,
                user_id=getattr(getattr(ctx, "author", None), "id", None),
                repo_path=repo_path,
            )
            await aider_bot.run_prototype(
                key=key,
                repo_path=repo_path,
                user_id=getattr(getattr(ctx, "author", None), "id", 0) or 0,
                prompt=prompt,
                model=model,
            )
            await ctx.send("📋 Product is drafting requirements...")

    return bot
