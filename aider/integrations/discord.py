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
from aider.company.audit import AuditLogViewer
from aider.company.config import default_company_config
from aider.agent.loop import AgentLoopConfig
from aider.company.approval import ApprovalManager
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.project import Project
from aider.company.departments.devops import DevOpsDepartment
from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.product import ProductDepartment
from aider.company.departments.qa import QADepartment
from aider.company.departments.ux import UXDepartment
from aider.company.schemas import CompanyEvent, CompanyTask, EventMessage
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


def format_approval_required_message(event: EventMessage) -> str:
    payload = event.payload
    project_name = payload.get("project_name") or "unknown-project"
    gate_name = payload.get("gate_name", "prd_approval")
    gate_label = (
        gate_name.replace("prd", "PRD").replace("_", " ").title().replace("Prd", "PRD")
    )
    handoff_to = payload.get("handoff_to") or "engineering"
    handoff_label = str(handoff_to).replace("_", " ").title()
    title = (
        "📋 **Product Department Deliverable Ready**"
        if gate_name == "prd_approval"
        else "🧪 **QA Release Approval Required**"
    )
    preview = str(payload.get("artifact_preview", "")).strip()
    quoted_preview = "\n".join(
        f"> {line}" if line else ">" for line in preview.splitlines()
    )
    return (
        f"{title}\n"
        f"Project: `{project_name}`\n"
        f"Gate: {gate_label} → {handoff_label}\n\n"
        "**Preview:**\n"
        f"{quoted_preview}"
    )


def format_lifecycle_event_message(event: EventMessage) -> str:
    payload = event.payload or {}
    event_name = payload.get("name") or str(event.event)
    label = str(event_name).replace("_", " ").title()
    iteration = payload.get("iteration")
    suffix = f" (iteration {iteration})" if iteration is not None else ""
    details = []
    files = payload.get("files") or []
    if files:
        details.append("Files: " + ", ".join(str(path) for path in files[:8]))
    feedback = payload.get("feedback") or {}
    if isinstance(feedback, dict) and feedback.get("summary"):
        details.append("Review: " + str(feedback.get("summary")))
    checks = payload.get("checks") or []
    if checks:
        passed = sum(1 for check in checks if check.get("status") == "passed")
        details.append(f"Checks: {passed}/{len(checks)} passed")
    body = "\n".join(details)
    if body:
        body = "\n" + body
    return f"🔄 **{label}**{suffix}\nTask: `{event.task_id}`{body}"


def format_audit_log_message(project_memory: ProjectMemory, limit: int = 10) -> str:
    viewer = AuditLogViewer.from_project_memory(project_memory)
    rendered = viewer.render_text(limit=limit)
    return f"🧾 **Recent Audit Events**\n```\n{rendered[:1800]}\n```"


def format_company_status_message(orchestrator: CompanyOrchestrator) -> str:
    rendered = orchestrator.company_status()
    return f"🏢 **Company Dashboard**\n```\n{rendered[:1800]}\n```"


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
        self.ux: Optional[UXDepartment] = None
        self.qa: Optional[QADepartment] = None
        self.devops: Optional[DevOpsDepartment] = None
        self.active_project: Optional[Project] = None

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
        company_event_callback: Optional[Callable[[object], Awaitable[None]]] = None,
    ) -> EngineeringDepartment:
        company_config = default_company_config()
        agent_loop = AiderAgentLoop(
            coder=coder,
            callback=callback,
            config=AgentLoopConfig(
                use_architect_mode=self.config.use_architect_mode,
                architect_model=self.config.architect_model,
                editor_model=self.config.editor_model,
            ),
            enable_prompt_caching=company_config.default_enable_caching,
        )
        self.engineering = EngineeringDepartment(
            project_memory=coder.project_memory,
            agent_loop=agent_loop,
            conversation_memory=coder.conversation_memory,
            config=company_config.get_department_config("engineering"),
        )
        self.product = ProductDepartment(
            project_memory=coder.project_memory,
            conversation_memory=None,
            config=company_config.get_department_config("product"),
        )
        self.ux = UXDepartment(
            project_memory=coder.project_memory,
            conversation_memory=None,
            config=company_config.get_department_config("ux"),
        )
        self.qa = QADepartment(
            project_memory=coder.project_memory,
            conversation_memory=None,
            config=company_config.get_department_config("qa"),
        )
        self.devops = DevOpsDepartment(
            project_memory=coder.project_memory,
            conversation_memory=None,
            config=company_config.get_department_config("devops"),
        )
        self.orchestrator = CompanyOrchestrator(
            project_memory=coder.project_memory,
            company_config=company_config,
        )
        self.orchestrator.active_project = self.active_project
        self.orchestrator.register(self.product)
        self.orchestrator.register(self.ux)
        self.orchestrator.register(self.engineering)
        self.orchestrator.register(self.qa)
        self.orchestrator.register(self.devops)
        if company_event_callback:
            self.orchestrator.on_deliverable(company_event_callback)
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
            self.active_project = Project(
                project_id=str(uuid.uuid4()),
                name=Path(repo_path).name,
                phase="prototyping",
            )
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
        company_event_callback: Optional[Callable[[object], Awaitable[None]]] = None,
    ):
        """Start a new project by routing the prompt through Product before Engineering."""
        self.check_access(user_id)
        if len(prompt) > self.config.max_prompt_chars:
            raise ValueError("Prompt too large")

        coder = await self.get_or_create_session(key, repo_path, model=model)
        self.on_reconnect_or_ping(key)
        self._init_company_session(
            coder,
            callback=callback,
            company_event_callback=company_event_callback,
        )
        if self.orchestrator and isinstance(self.orchestrator.memory, ProjectMemory):
            await self.orchestrator.recover_pending_approvals()

        if not self.active_project:
            self.active_project = Project(
                project_id=str(uuid.uuid4()),
                name=Path(repo_path).name,
                phase="prototyping",
            )
        if self.orchestrator:
            self.orchestrator.active_project = self.active_project

        task = CompanyTask(
            task_id=str(uuid.uuid4()),
            origin="ceo",
            target="product",
            artifact_type="raw_prompt",
            payload=prompt,
            blocking=False,
            context={"project_name": Path(repo_path).name},
        )

        deliverable = await self.product.process(task)
        if self.orchestrator:
            asyncio.create_task(self.orchestrator._route(deliverable))

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
        if self.orchestrator and isinstance(self.orchestrator.memory, ProjectMemory):
            await self.orchestrator.recover_pending_approvals()
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
            deliverable = await asyncio.wait_for(
                run_task, timeout=self.config.max_runtime_seconds
            )
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
            project_memory.update(
                {"last_prompt": prompt, "last_result": result_content}
            )

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

    class ApprovalFeedbackModal(discord.ui.Modal):
        def __init__(
            self, approval_manager: ApprovalManager, task_id: str, gate_name: str
        ):
            title = (
                "Request Release Changes"
                if gate_name == "release_approval"
                else "Request PRD Changes"
            )
            label = (
                "Feedback for Engineering"
                if gate_name == "release_approval"
                else "Feedback for Product"
            )
            super().__init__(title=title)
            self.approval_manager = approval_manager
            self.task_id = task_id
            self.gate_name = gate_name
            self.feedback = discord.ui.TextInput(
                label=label,
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1500,
            )
            self.add_item(self.feedback)

        async def on_submit(self, interaction):
            await self.approval_manager.handle_approval_response(
                self.task_id,
                False,
                source="discord",
                reason=str(self.feedback.value),
                metadata={"action": "revise", "feedback": str(self.feedback.value)},
            )
            destination = (
                "Engineering" if self.gate_name == "release_approval" else "Product"
            )
            await interaction.response.send_message(
                f"📝 Change request sent back to {destination}.",
                ephemeral=True,
            )

    class ApprovalView(discord.ui.View):
        def __init__(
            self, approval_manager: ApprovalManager, task_id: str, gate_name: str
        ):
            super().__init__(timeout=None)
            self.approval_manager = approval_manager
            self.task_id = task_id
            self.gate_name = gate_name

        @discord.ui.button(
            label="Approve", emoji="✅", style=discord.ButtonStyle.success
        )
        async def approve_button(self, interaction, button):
            resolved = await self.approval_manager.handle_approval_response(
                self.task_id, True, source="discord"
            )
            for child in self.children:
                child.disabled = True
            content = (
                "This approval was already resolved from another message."
                if not resolved
                else (
                    "✅ Release approved. DevOps deployment has started."
                    if self.gate_name == "release_approval"
                    else "✅ PRD approved. Engineering handoff has started."
                )
            )
            await interaction.response.edit_message(content=content, view=self)

        @discord.ui.button(label="Reject", emoji="❌", style=discord.ButtonStyle.danger)
        async def reject_button(self, interaction, button):
            resolved = await self.approval_manager.handle_approval_response(
                self.task_id, False, source="discord"
            )
            for child in self.children:
                child.disabled = True
            content = (
                "This approval was already resolved from another message."
                if not resolved
                else (
                    "❌ Release rejected and routed back to Engineering."
                    if self.gate_name == "release_approval"
                    else "❌ PRD rejected and routed back to Product."
                )
            )
            await interaction.response.edit_message(content=content, view=self)

        @discord.ui.button(
            label="Request Changes", emoji="📝", style=discord.ButtonStyle.secondary
        )
        async def request_changes_button(self, interaction, button):
            await interaction.response.send_modal(
                ApprovalFeedbackModal(
                    self.approval_manager, self.task_id, self.gate_name
                )
            )

    async def send_company_event(ctx, event):
        if not isinstance(event, EventMessage):
            return
        if event.event == CompanyEvent.LIFECYCLE:
            await ctx.send(format_lifecycle_event_message(event))
            return
        if event.event != CompanyEvent.APPROVAL_REQUIRED:
            return
        orchestrator = aider_bot.orchestrator
        if orchestrator is None:
            return
        approval_manager = orchestrator.approval_manager
        await ctx.send(
            format_approval_required_message(event),
            view=ApprovalView(
                approval_manager,
                event.task_id,
                event.payload.get("gate_name", "prd_approval"),
            ),
        )

    if aider_bot is not None:

        @bot.command(name="audit")
        async def audit(ctx, limit: int = 10):
            repo_path = (
                repo_path_resolver(ctx)
                if repo_path_resolver
                else getattr(ctx, "repo_path", None)
            )
            key = DiscordSessionKey(
                guild_id=getattr(getattr(ctx, "guild", None), "id", 0) or 0,
                channel_id=ctx.channel.id,
                user_id=getattr(getattr(ctx, "author", None), "id", None),
                repo_path=repo_path,
            )
            project_memory = aider_bot.sessions._project_memories.get(key)
            if project_memory is None and aider_bot.orchestrator is not None:
                project_memory = aider_bot.orchestrator.memory
            if project_memory is None:
                await ctx.send("No project memory is available for audit logs yet.")
                return
            await ctx.send(
                format_audit_log_message(project_memory, limit=max(1, min(limit, 25)))
            )


        @bot.command(name="company_status")
        async def company_status(ctx):
            if aider_bot.orchestrator is None:
                await ctx.send("No company session is active yet.")
                return
            await ctx.send(format_company_status_message(aider_bot.orchestrator))

        @bot.command(name="dashboard")
        async def dashboard(ctx):
            if aider_bot.orchestrator is None:
                await ctx.send("No company session is active yet.")
                return
            await ctx.send(format_company_status_message(aider_bot.orchestrator))

        @bot.command(name="prototype")
        async def prototype(ctx, *, prompt: str):
            repo_path = (
                repo_path_resolver(ctx)
                if repo_path_resolver
                else getattr(ctx, "repo_path", None)
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
                company_event_callback=lambda event: send_company_event(ctx, event),
            )
            await ctx.send("📋 Product is drafting requirements...")

    return bot
