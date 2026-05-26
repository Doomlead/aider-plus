"""Discord chat-app adapter for Aider Plus.

Discord intentionally stays a thin chat surface. Company workflow, approvals,
audit logs, COO status, lifecycle formatting, memory, MCP, and deployment logic
live in the shared Company/browser/desktop layers instead of this integration.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional, Set

from aider.coders import Coder
from aider.company.events import CompanyEvent as RuntimeCompanyEvent, EventBus
from aider.integrations.adapters import HIGH_PRIORITY_EVENT_TYPES, ThinAdapter
from aider.company.surface_messages import (
    format_approval_required_message,
    format_audit_log_message,
    format_company_status_message,
    format_coo_status_message,
    format_lifecycle_event_message,
    format_discord_event_block,
    format_runtime_event_message,
)
from aider.main import main as aider_main
from aider.company.runtime import CompanyRunRequest, run_company_task
from aider.company.schemas import CompanyTask
from aider.memory import ConversationMemory, ProjectMemory, consolidate_conversation
from aider.memory import communication as communication_memory


def subscribe_discord_event_forwarder(
    event_bus: EventBus,
    forward: Callable[[str], Awaitable[None] | None],
    *,
    event_types: Set[str] | None = None,
):
    """Forward selected high-priority Company EventBus events to Discord."""

    adapter = ThinAdapter(
        event_bus=event_bus,
        forward=forward,
        event_formatter=lambda event: (
            format_discord_event_block(event)
            if event.event_type in HIGH_PRIORITY_EVENT_TYPES or event.severity != "info"
            else format_runtime_event_message(event)
        ),
    )
    return adapter.subscribe_to_bus(event_types=event_types)


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
    """In-memory coder sessions keyed by Discord channel/user/repo."""

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

    def attach_project_memory(
        self, key: DiscordSessionKey, project_memory: ProjectMemory
    ):
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


class DiscordAiderBot(ThinAdapter):
    """Thin async façade for Discord chat messages.

    This adapter only turns Discord text into a headless Aider chat request and
    returns text back to Discord. Product workflow, COO routing, approval gates,
    dashboards, audit views, and deployment controls should be used through the
    browser GUI, desktop GUI, CLI, API, or MCP layers.
    """

    surface_name = "discord"

    def __init__(self, config: Optional[DiscordAiderConfig] = None, **adapter_kwargs):
        super().__init__(**adapter_kwargs)
        self.config = config or DiscordAiderConfig()
        self.sessions = DiscordSessionManager()

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
        return await self.run_instruction(
            key=key,
            repo_path=repo_path,
            user_id=user_id,
            prompt=prompt,
            model=model,
            callback=callback,
        )

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
        """Run one Discord chat message through classic headless Aider."""
        normalized = self.normalize_message(
            prompt,
            user_id=user_id,
            channel_id=key.channel_id,
            thread_id=key.channel_id,
            repo_path=repo_path,
        )
        prompt = normalized.text
        self.check_access(user_id)
        if len(prompt) > self.config.max_prompt_chars:
            raise ValueError("Prompt too large")

        coder = await self.get_or_create_session(key, repo_path, model=model)
        self.on_reconnect_or_ping(key)
        project_memory = getattr(coder, "project_memory", None)
        if isinstance(project_memory, ProjectMemory):
            communication_memory.user_instruction(
                project_memory,
                prompt,
                surface="discord",
                session_id=str(key.channel_id),
                origin=str(user_id),
                metadata={"repo_path": repo_path},
            )

        async def _execute(task, _metadata):
            orchestrator = getattr(coder, "orchestrator", None)
            if orchestrator is not None and hasattr(orchestrator, "submit"):
                await orchestrator.submit(task)
                project = getattr(orchestrator, "active_project", None)
                deliverable = getattr(project, "engineering_result", None) if project else None
                if deliverable is not None:
                    return {
                        "summary": str(
                            getattr(deliverable, "summary", "")
                            or getattr(deliverable, "payload", "")
                            or ""
                        ),
                        "status": getattr(deliverable, "status", "success"),
                    }
                return {"summary": "", "status": "success"}
            return await asyncio.to_thread(coder.run, str(task.payload))

        req = CompanyRunRequest(
            surface="discord",
            session_id=f"discord:{key.channel_id}",
            task=CompanyTask(
                task_id=f"discord:{key.channel_id}:{int(time.time())}",
                origin="user",
                target="engineering",
                artifact_type="raw_prompt",
                payload=prompt,
                blocking=False,
            ),
            metadata={"user_id": user_id, "repo_path": repo_path},
        )

        try:
            content = await asyncio.wait_for(
                run_company_task(req, execute=_execute), timeout=self.config.max_runtime_seconds
            )
        except asyncio.TimeoutError as err:
            raise TimeoutError("Aider request timed out") from err

        if isinstance(content, dict):
            result_content = str(content.get("summary") or content.get("content") or "")
        else:
            result_content = content if isinstance(content, str) else str(content or "")
        project_memory = getattr(coder, "project_memory", None)
        if isinstance(project_memory, ProjectMemory):
            project_memory.update(
                {"last_prompt": prompt, "last_result": result_content}
            )

        result = {
            "summary": result_content,
            "content": result_content,
            "files_changed": [],
            "files": [],
            "commits": [],
            "diffs": [],
            "status": "success",
        }
        if callback:
            await callback(result_content, result)
        return result

    def on_disconnect(self, key: DiscordSessionKey):
        """Persist project memory when a Discord chat session disconnects."""
        self.sessions.persist_project_memory(key)

    def on_reconnect_or_ping(self, key: DiscordSessionKey):
        """Refresh project memory so new chat messages receive persisted context."""
        coder = self.sessions.get(key)
        if not coder:
            return
        project_memory = getattr(coder, "project_memory", None)
        if isinstance(project_memory, ProjectMemory):
            project_memory.load()


async def _maybe_send_response(ctx, text: str):
    if hasattr(ctx, "send"):
        await ctx.send(text[:1900] or "Done.")
    else:
        await ctx.response.send_message(text[:1900] or "Done.")


def build_discord_client(*args, **kwargs):
    """Build a discord.py client that only forwards chat text to Aider."""

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

        async def run_chat(ctx, prompt: str):
            repo_path = (
                repo_path_resolver(ctx)
                if repo_path_resolver
                else getattr(ctx, "repo_path", None)
            )
            if not repo_path:
                await _maybe_send_response(
                    ctx, "Repository path is required for Discord chat."
                )
                return
            model = model_resolver(ctx) if model_resolver else None
            channel = getattr(ctx, "channel", None)
            author = getattr(ctx, "author", None) or getattr(ctx, "user", None)
            key = DiscordSessionKey(
                guild_id=getattr(getattr(ctx, "guild", None), "id", 0) or 0,
                channel_id=getattr(channel, "id", None)
                or getattr(ctx, "channel_id", 0)
                or 0,
                user_id=getattr(author, "id", None),
                repo_path=repo_path,
            )
            result = await aider_bot.run_instruction(
                key=key,
                repo_path=repo_path,
                user_id=getattr(author, "id", 0) or 0,
                prompt=prompt,
                model=model,
            )
            await _maybe_send_response(
                ctx, result.get("content") or result.get("summary") or "Done."
            )

        @bot.command(name="chat")
        async def chat(ctx, *, prompt: str):
            await run_chat(ctx, prompt)

        @bot.tree.command(name="chat", description="Send a chat message to Aider")
        async def chat_slash(interaction, prompt: str):
            await run_chat(interaction, prompt)

    return bot
