from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional

from aider.company.daemon import CompanyDaemonError, load_daemon
from aider.company.events import CooActionTaken, EventBus, global_event_bus
from aider.company.knowledge import KnowledgeManager
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.workflow import WorkflowError
from aider.company.schemas import CompanyTask, Deliverable
from aider.company.skills import CompanySkillManager
from aider.memory import ProjectMemory
from aider.memory import communication as communication_memory


@dataclass
class COOMessage:
    """Message envelope used by the local Nanobot-inspired COO bus."""

    channel: str
    session_key: str
    content: str
    role: str = "user"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class COOMessageBusEvent:
    """Observable event emitted when messages move through the COO bus."""

    event_type: str
    channel: str
    session_key: str
    role: str
    queue: str
    queue_size: int
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "channel": self.channel,
            "session_key": self.session_key,
            "role": self.role,
            "queue": self.queue,
            "queue_size": self.queue_size,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


BusEventHandler = Callable[[COOMessageBusEvent], Awaitable[None] | None]


class COOMessageBus:
    """Small async bus that decouples user channels from company orchestration."""

    def __init__(self, *, history_limit: int = 200, event_bus: EventBus | None = None):
        self.inbound: asyncio.Queue[COOMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[COOMessage] = asyncio.Queue()
        self.history_limit = history_limit
        self.events: list[COOMessageBusEvent] = []
        self.event_bus = event_bus or global_event_bus
        self._handlers: list[BusEventHandler] = []
        self.stats = {
            "inbound_published": 0,
            "inbound_consumed": 0,
            "outbound_published": 0,
            "outbound_consumed": 0,
        }

    def on_event(self, handler: BusEventHandler) -> None:
        self._handlers.append(handler)

    async def publish_inbound(self, message: COOMessage) -> COOMessageBusEvent:
        await self.inbound.put(message)
        self.stats["inbound_published"] += 1
        return await self._record(
            "message_published", message, "inbound", self.inbound_size
        )

    async def consume_inbound(self) -> COOMessage:
        message = await self.inbound.get()
        self.stats["inbound_consumed"] += 1
        await self._record("message_consumed", message, "inbound", self.inbound_size)
        return message

    async def publish_outbound(self, message: COOMessage) -> COOMessageBusEvent:
        await self.outbound.put(message)
        self.stats["outbound_published"] += 1
        return await self._record(
            "message_published", message, "outbound", self.outbound_size
        )

    async def consume_outbound(self) -> COOMessage:
        message = await self.outbound.get()
        self.stats["outbound_consumed"] += 1
        await self._record("message_consumed", message, "outbound", self.outbound_size)
        return message

    async def emit_error(
        self,
        *,
        session_key: str,
        channel: str,
        metadata: dict[str, Any],
    ) -> COOMessageBusEvent:
        """Emit a warning-severity COO error event without queueing a message."""
        message = COOMessage(
            channel=channel,
            session_key=session_key,
            role="system",
            content=str(metadata.get("error_type") or "coo_error"),
            metadata={"severity": "warning", **metadata},
        )
        return await self._record("coo_error", message, "error", 0)

    async def _record(
        self,
        event_type: str,
        message: COOMessage,
        queue: str,
        queue_size: int,
    ) -> COOMessageBusEvent:
        metadata = {"message_metadata": dict(message.metadata)}
        if event_type == "coo_error":
            metadata.update(message.metadata)
        event = COOMessageBusEvent(
            event_type=event_type,
            channel=message.channel,
            session_key=message.session_key,
            role=message.role,
            queue=queue,
            queue_size=queue_size,
            metadata=metadata,
        )
        self.events.append(event)
        if len(self.events) > self.history_limit:
            self.events = self.events[-self.history_limit :]
        await self.event_bus.publish_async(
            CooActionTaken(
                session_id=message.session_key,
                severity="warning" if event_type == "coo_error" else "info",
                payload={
                    "coo_event_type": event_type,
                    "channel": message.channel,
                    "role": message.role,
                    "queue": queue,
                    "queue_size": queue_size,
                    "content": message.content,
                    "metadata": metadata,
                },
            )
        )
        for handler in list(self._handlers):
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        return event

    def get_formatted_events(self, limit: int = 20) -> list[str]:
        """Return recent bus events as dashboard-friendly human strings."""
        formatted: list[str] = []
        for event in self.events[-max(1, limit) :]:
            message_metadata = event.metadata.get("message_metadata", {})
            route = message_metadata.get("route") or {}
            coo_action = message_metadata.get("coo_action") or {}
            target = message_metadata.get("department") or message_metadata.get(
                "target"
            )
            task_id = message_metadata.get("task_id")
            details = [
                f"{event.event_type.replace('_', ' ')}",
                f"{event.queue} queue={event.queue_size}",
                f"{event.role} via {event.channel}",
            ]
            if event.event_type == "coo_error":
                error_type = message_metadata.get("error_type", "unknown_error")
                retries = message_metadata.get("retries", 0)
                details.append(f"warning {error_type} after {retries} retries")
                recovery = message_metadata.get("recovery_suggestion")
                if recovery:
                    details.append(f"recovery: {recovery}")
                if message_metadata.get("escalate_to_human"):
                    details.append("human escalation pending")
            if coo_action:
                details.append(f"action {coo_action.get('action', 'unknown')}")
            if route:
                details.append(
                    "route "
                    f"{route.get('strategy', 'unknown')} → {route.get('target', 'unknown')}"
                )
            elif target:
                details.append(f"handoff → {target}")
            if task_id:
                details.append(f"task {task_id}")
            formatted.append(
                f"[{event.created_at}] {event.session_key}: " + " | ".join(details)
            )
        return formatted

    def snapshot(self) -> dict[str, Any]:
        return {
            "inbound_size": self.inbound_size,
            "outbound_size": self.outbound_size,
            "stats": dict(self.stats),
            "recent_events": [event.as_dict() for event in self.events[-20:]],
            "formatted_events": self.get_formatted_events(limit=20),
        }

    @property
    def inbound_size(self) -> int:
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        return self.outbound.qsize()


@dataclass
class COOSession:
    """Durable conversation/session state for one user/channel thread."""

    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def add_message(self, role: str, content: str, **metadata: Any) -> None:
        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
                **metadata,
            }
        )
        self.updated_at = datetime.utcnow().isoformat()

    def get_history(self, max_messages: int = 40) -> list[dict[str, Any]]:
        return self.messages[-max(1, max_messages) :]

    def snapshot(self, *, recent_limit: int = 10) -> dict[str, Any]:
        """Return a compact, UI-safe view of persisted COO session state."""
        recent_events = self.get_history(recent_limit)
        route_history = []
        for message in self.messages:
            route = message.get("route") or message.get("metadata", {}).get("route")
            if route:
                route_history.append(route)
        last_route = self.metadata.get("last_route")
        if last_route and (not route_history or route_history[-1] != last_route):
            route_history.append(last_route)
        last_assistant = next(
            (
                message
                for message in reversed(self.messages)
                if message.get("role") == "assistant"
            ),
            None,
        )
        pending_escalations = list(
            self.metadata.get("pending_human_escalations", []) or []
        )
        recent_errors = list(self.metadata.get("recent_errors", []) or [])
        if self.metadata.get("last_error") and (
            not recent_errors or recent_errors[-1] != self.metadata.get("last_error")
        ):
            recent_errors.append(self.metadata["last_error"])
        return {
            "key": self.key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "metadata": dict(self.metadata),
            "recent_events": recent_events,
            "route_history": route_history[-recent_limit:],
            "active_department": self.metadata.get("last_target"),
            "last_deliverable_summary": (
                last_assistant.get("content") if last_assistant else None
            ),
            "error_count": int(self.metadata.get("error_count", 0) or 0),
            "last_error": self.metadata.get("last_error"),
            "recent_errors": recent_errors[-recent_limit:],
            "pending_human_escalations": pending_escalations,
            "last_human_escalation": self.metadata.get("last_human_escalation"),
        }


class COOSessionManager:
    """JSONL session store modeled after Nanobot's lightweight session manager."""

    def __init__(self, project_memory: ProjectMemory, dirname: str = "coo_sessions"):
        self.project_memory = project_memory
        self.sessions_dir = Path(project_memory.repo_path) / ".aider" / dirname
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, COOSession] = {}

    @staticmethod
    def safe_key(key: str) -> str:
        safe = "".join(
            ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in key
        )
        return safe[:160] or "default"

    def _path(self, key: str) -> Path:
        return self.sessions_dir / f"{self.safe_key(key)}.jsonl"

    def get_or_create(self, key: str) -> COOSession:
        if key in self._cache:
            return self._cache[key]
        session = self._load(key) or COOSession(key=key)
        self._cache[key] = session
        return session

    def _load(self, key: str) -> Optional[COOSession]:
        path = self._path(key)
        if not path.exists():
            return None
        messages: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {}
        created_at: Optional[str] = None
        updated_at: Optional[str] = None
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("_type") == "metadata":
                    metadata = payload.get("metadata", {}) or {}
                    created_at = payload.get("created_at")
                    updated_at = payload.get("updated_at")
                else:
                    messages.append(payload)
        return COOSession(
            key=key,
            messages=messages,
            metadata=metadata,
            created_at=created_at or datetime.utcnow().isoformat(),
            updated_at=updated_at or datetime.utcnow().isoformat(),
        )

    def save(self, session: COOSession) -> None:
        path = self._path(session.key)
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "_type": "metadata",
                        "key": session.key,
                        "created_at": session.created_at,
                        "updated_at": session.updated_at,
                        "metadata": session.metadata,
                    },
                    ensure_ascii=False,
                )
                + os.linesep
            )
            for message in session.messages:
                handle.write(json.dumps(message, ensure_ascii=False) + os.linesep)
        os.replace(tmp, path)
        self._cache[session.key] = session

    def flush_all(self) -> int:
        for session in list(self._cache.values()):
            self.save(session)
        return len(self._cache)


@dataclass
class COOActionDecision:
    """High-level CEO-facing action chosen by the Nanobot-style COO.

    The COO first decides how to serve the CEO: answer directly, clarify,
    inspect status, update memory, use a tool, or delegate into the existing
    CompanyOrchestrator. Department routing is one possible action, not the
    whole COO job.
    """

    action: Literal[
        "answer_directly",
        "ask_ceo_clarification",
        "delegate_company_task",
        "inspect_status",
        "inspect_skills",
        "inspect_knowledge",
        "search_knowledge",
        "list_daemon_workflows",
        "update_memory",
        "recall_memory",
        "use_tool",
    ] = "delegate_company_task"
    response_to_ceo: str = ""
    confidence: float = 0.5
    requires_approval: bool = False
    company_target: str | None = None
    tool_name: str | None = None
    memory_updates: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    route: "COORouteDecision | None" = None
    reasoning: str = ""

    def __post_init__(self) -> None:
        allowed = {
            "answer_directly",
            "ask_ceo_clarification",
            "delegate_company_task",
            "inspect_status",
            "inspect_skills",
            "inspect_knowledge",
            "search_knowledge",
            "list_daemon_workflows",
            "update_memory",
            "recall_memory",
            "use_tool",
        }
        aliases = {
            "answer": "answer_directly",
            "clarify": "ask_ceo_clarification",
            "ask_clarification": "ask_ceo_clarification",
            "delegate": "delegate_company_task",
            "route": "delegate_company_task",
            "status": "inspect_status",
            "skills": "inspect_skills",
            "knowledge": "inspect_knowledge",
            "search": "search_knowledge",
            "daemon": "list_daemon_workflows",
            "remember": "update_memory",
            "recall": "recall_memory",
            "tool": "use_tool",
        }
        action = str(self.action or "delegate_company_task").strip().lower()
        action = aliases.get(action, action)
        if action not in allowed:
            action = "delegate_company_task"
        self.action = action  # type: ignore[assignment]
        self.response_to_ceo = str(self.response_to_ceo or "").strip()
        try:
            self.confidence = float(self.confidence)
        except (TypeError, ValueError):
            self.confidence = 0.5
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.requires_approval = bool(self.requires_approval)
        if self.company_target is not None:
            self.company_target = str(self.company_target).strip().lower() or None
        if self.tool_name is not None:
            self.tool_name = str(self.tool_name).strip() or None
        self.memory_updates = [
            update for update in self.memory_updates or [] if isinstance(update, dict)
        ]
        self.context = dict(self.context or {})
        self.reasoning = str(
            self.reasoning or self.context.get("reasoning") or ""
        ).strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "response_to_ceo": self.response_to_ceo,
            "confidence": self.confidence,
            "requires_approval": self.requires_approval,
            "company_target": self.company_target,
            "tool_name": self.tool_name,
            "memory_updates": self.memory_updates,
            "context": self.context,
            "route": self.route.as_dict() if self.route else None,
            "reasoning": self.reasoning,
        }


@dataclass
class COORouteDecision:
    """Explicit COO routing result for a user message."""

    target: str = ""
    strategy: str = "deterministic"
    reason: str = "Route selected by COO"
    confidence: float = 0.5
    should_escalate_to_human: bool = False
    escalate_to_human: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    chosen_department: str | None = None
    reasoning: str | None = None

    def __post_init__(self) -> None:
        target = (
            self.chosen_department
            if self.chosen_department is not None
            else self.target
        )
        self.target = str(target or "").strip().lower()
        self.chosen_department = self.target
        self.strategy = str(self.strategy or "deterministic").strip().lower()
        reason = self.reasoning if self.reasoning is not None else self.reason
        self.reason = str(reason or "Route selected by COO").strip()
        self.reasoning = self.reason
        try:
            self.confidence = float(self.confidence)
        except (TypeError, ValueError):
            self.confidence = 0.5
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.escalate_to_human = bool(self.escalate_to_human)
        self.should_escalate_to_human = bool(
            self.should_escalate_to_human or self.escalate_to_human
        )
        self.escalate_to_human = self.should_escalate_to_human
        self.metadata = dict(self.metadata or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "chosen_department": self.chosen_department,
            "strategy": self.strategy,
            "reason": self.reason,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "should_escalate_to_human": self.should_escalate_to_human,
            "escalate_to_human": self.escalate_to_human,
            "metadata": self.metadata,
        }


class NanobotCOO:
    """Thin coordinator for COO sessions, routing, and orchestration handoff."""

    def __init__(
        self,
        *,
        orchestrator: CompanyOrchestrator,
        coo_agent_loop=None,
        agent_loop=None,
        session_manager: COOSessionManager | None = None,
        bus: COOMessageBus | None = None,
        default_target: str = "product",
        enable_llm_routing: bool | None = None,
    ):
        self.orchestrator = orchestrator
        self.coo_agent_loop = (
            coo_agent_loop if coo_agent_loop is not None else agent_loop
        )
        self.session_manager = session_manager or COOSessionManager(orchestrator.memory)
        self.bus = bus or COOMessageBus(event_bus=orchestrator.event_bus)
        self.default_target = default_target
        self.agent_config = orchestrator.company_config.get_department_config("coo")
        if self.coo_agent_loop is not None:
            self.coo_agent_loop.enable_prompt_caching = self.agent_config.enable_caching
            loop_config = getattr(self.coo_agent_loop, "config", None)
            if loop_config is not None:
                loop_config.enable_caching = self.agent_config.enable_caching
                loop_config.cache_type = self.agent_config.cache_type
        self.enable_llm_routing = (
            orchestrator.company_config.enable_coo_llm_routing
            if enable_llm_routing is None
            else enable_llm_routing
        )

    @property
    def agent_loop(self):
        """Backward-compatible alias for the COO routing agent loop."""
        return self.coo_agent_loop

    async def _call_with_retry(
        self,
        coro: Callable[[], Awaitable[Any]] | Awaitable[Any],
        max_retries: int = 3,
        base_delay: float = 1.0,
        on_final_failure: (
            Callable[[BaseException], Awaitable[None] | None] | None
        ) = None,
    ) -> Any:
        """Call an async operation with bounded exponential backoff."""
        attempts = max(0, int(max_retries)) + 1 if callable(coro) else 1
        last_error: BaseException | None = None
        for attempt in range(attempts):
            try:
                operation = coro() if callable(coro) else coro
                return await operation
            except Exception as err:
                last_error = err
                if attempt >= attempts - 1:
                    break
                delay = max(0.0, base_delay) * (2**attempt)
                if delay:
                    await asyncio.sleep(delay)
        assert last_error is not None
        if on_final_failure is not None:
            result = on_final_failure(last_error)
            if asyncio.iscoroutine(result):
                await result
        raise last_error

    async def _emit_coo_error(
        self,
        *,
        session: COOSession | None,
        session_id: str,
        surface: str,
        error: BaseException,
        error_type: str,
        retries: int,
        user_message: str,
        recovery_suggestion: str,
        escalate_to_human: bool = False,
        approval_task_id: str | None = None,
    ) -> None:
        preview = user_message.replace("\n", " ")[:160]
        error_payload = {
            "error_type": error_type,
            "retries": retries,
            "user_message_preview": preview,
            "message": str(error),
            "error_class": error.__class__.__name__,
            "recovery_suggestion": recovery_suggestion,
            "escalate_to_human": bool(escalate_to_human),
        }
        if approval_task_id:
            error_payload["approval_task_id"] = approval_task_id
        if session is not None:
            session.metadata["error_count"] = (
                int(session.metadata.get("error_count", 0) or 0) + 1
            )
            session.metadata["last_error"] = error_payload
            recent_errors = list(session.metadata.get("recent_errors", []) or [])
            recent_errors.append(error_payload)
            session.metadata["recent_errors"] = recent_errors[-10:]
            self.session_manager.save(session)
        await self.bus.emit_error(
            session_key=session_id,
            channel=surface,
            metadata=error_payload,
        )

    async def _escalate_to_human(
        self,
        session: COOSession,
        user_message: str,
        error_details: dict[str, Any],
    ) -> CompanyTask:
        """Open a blocking human approval gate for unrecoverable COO failures."""
        target = error_details.get("fallback_target") or session.metadata.get(
            "last_target"
        )
        if target not in self.orchestrator.departments:
            target = (
                self.default_target
                if self.default_target in self.orchestrator.departments
                else None
            )
        if target is None and self.orchestrator.departments:
            target = next(iter(self.orchestrator.departments))
        target = target or "coo"
        task = CompanyTask(
            task_id=str(
                error_details.get("approval_task_id")
                or f"coo-escalation-{uuid.uuid4()}"
            ),
            origin="coo",
            target=target,
            artifact_type="coo_escalation",
            payload=user_message,
            blocking=True,
            context={
                "gate_name": "coo_human_escalation",
                "approver_role": "human",
                "handoff_to": target,
                "artifact_preview": (
                    "COO needs human routing assistance after retry exhaustion.\n\n"
                    f"User message: {user_message[:700]}\n\n"
                    f"Error: {error_details.get('error_type', 'coo_error')} — "
                    f"{error_details.get('message', '')}\n\n"
                    "Suggested recovery: "
                    f"{error_details.get('recovery_suggestion', 'review and choose the next action')}"
                ),
                "coo_error": error_details,
                "session_key": session.key,
            },
        )
        session.metadata.setdefault("pending_human_escalations", [])
        pending = session.metadata["pending_human_escalations"]
        if task.task_id not in pending:
            pending.append(task.task_id)
        session.metadata["last_human_escalation"] = {
            "task_id": task.task_id,
            "target": target,
            "error_type": error_details.get("error_type"),
            "recovery_suggestion": error_details.get("recovery_suggestion"),
            "created_at": datetime.utcnow().isoformat(),
        }
        self.session_manager.save(session)

        async def wait_for_decision() -> None:
            try:
                await self.orchestrator.approvals.create_request(task)
            finally:
                self.orchestrator.approvals.close_request(task.task_id)

        asyncio.create_task(wait_for_decision())
        await asyncio.sleep(0)
        return task

    async def receive_user_message(
        self,
        message: str | None = None,
        session_id: str | None = None,
        surface: str = "cli",
        **options: Any,
    ) -> dict[str, Any]:
        """Persist a user message, route it, hand it off, and return result/events."""
        message = message if message is not None else options.pop("prompt", None)
        session_id = (
            session_id if session_id is not None else options.pop("session_key", None)
        )
        surface = options.pop("channel", surface)
        if message is None:
            raise TypeError(
                "receive_user_message() missing required argument: 'message'"
            )
        if session_id is None:
            raise TypeError(
                "receive_user_message() missing required argument: 'session_id'"
            )

        target = options.pop("target", None)
        artifact_type = options.pop("artifact_type", "raw_prompt")
        context = options.pop("context", None)
        blocking = options.pop("blocking", False)
        wait = options.pop("wait", True)
        task_id = options.pop("task_id", None)
        origin = options.pop("origin", None)
        if options:
            unknown = ", ".join(sorted(options))
            raise TypeError(f"Unsupported COO message option(s): {unknown}")

        inbound = COOMessage(
            channel=surface,
            session_key=session_id,
            content=message,
            metadata={"target": target, "artifact_type": artifact_type},
        )
        await self.bus.publish_inbound(inbound)
        inbound = await self.bus.consume_inbound()

        session = self.session_manager.get_or_create(session_id)
        session.add_message(
            "user",
            inbound.content,
            surface=surface,
            metadata=inbound.metadata,
        )
        self.session_manager.save(session)

        payload = {
            "message": inbound.content,
            "surface": surface,
            "artifact_type": artifact_type,
            "context": context or {},
            "blocking": blocking,
            "wait": wait,
            "task_id": task_id,
            "origin": origin or surface,
        }
        communication_memory.user_instruction(
            self.orchestrator.memory,
            inbound.content,
            surface=surface,
            session_id=session_id,
            task_id=task_id,
            origin=origin or surface,
            target=target,
            metadata={"artifact_type": artifact_type},
        )
        return await self.run_personal_turn(
            session=session,
            message=inbound,
            target=target,
            payload=payload,
        )

    async def run_personal_turn(
        self,
        *,
        session: COOSession,
        message: COOMessage,
        target: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one CEO-facing COO assistant turn.

        The COO first acts like a persistent personal assistant: it may answer the
        CEO, ask for clarification, update/recall memory, inspect company status,
        or delegate into the existing CompanyOrchestrator. Delegation reuses the
        original department-routing path so Product→UX→Engineering→QA→DevOps
        orchestration remains unchanged.
        """
        payload = dict(payload or {})
        payload.setdefault("message", message.content)
        payload.setdefault("surface", message.channel)
        payload.setdefault("artifact_type", "raw_prompt")
        payload.setdefault("context", {})
        payload.setdefault("blocking", False)
        payload.setdefault("wait", True)
        payload.setdefault("origin", message.channel)

        action = await self.decide_action(message.content, session, target=target)
        session.messages[-1]["coo_action"] = action.as_dict()
        session.metadata["last_coo_action"] = action.as_dict()
        self.session_manager.save(session)

        if action.action == "delegate_company_task":
            route = action.route or COORouteDecision(
                target=action.company_target or target or self.default_target,
                strategy="coo_action",
                reason=action.reasoning
                or "COO delegated work to an internal department",
                confidence=action.confidence,
            )
            return await self.delegate_company_task(session, message, route, payload)

        return await self._complete_personal_action(session, message, action, payload)

    async def _delegate_with_route(
        self,
        session: COOSession,
        inbound: COOMessage,
        route: COORouteDecision,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        session.messages[-1]["route"] = route.as_dict()
        self.session_manager.save(session)

        if route.escalate_to_human:
            error_details = {
                "error_type": "coo_route_requires_human",
                "retries": 0,
                "message": route.reason,
                "error_class": "COORouteDecision",
                "recovery_suggestion": "escalating to human",
                "fallback_target": route.target,
            }
            escalation = await self._escalate_to_human(
                session, inbound.content, error_details
            )
            await self._emit_coo_error(
                session=session,
                session_id=session.key,
                surface=inbound.channel,
                error=RuntimeError(route.reason),
                error_type="coo_route_requires_human",
                retries=0,
                user_message=inbound.content,
                recovery_suggestion="escalating to human",
                escalate_to_human=True,
                approval_task_id=escalation.task_id,
            )

        payload = dict(payload)
        payload["route"] = route.as_dict()

        async def emit_primary_handoff_failure(err: BaseException) -> None:
            await self._emit_coo_error(
                session=session,
                session_id=session.key,
                surface=inbound.channel,
                error=err,
                error_type="department_handoff_failed",
                retries=3,
                user_message=inbound.content,
                recovery_suggestion="falling back to deterministic routing",
                escalate_to_human=False,
            )

        try:
            result = await self._call_with_retry(
                lambda: self.route_to_department(session, route.target, payload),
                on_final_failure=emit_primary_handoff_failure,
            )
        except Exception:
            fallback_route = self._deterministic_route(inbound.content)
            fallback_route.strategy = "deterministic_fallback"
            fallback_route.metadata["fallback_from"] = route.as_dict()
            session.messages[-1]["route"] = fallback_route.as_dict()
            self.session_manager.save(session)
            payload["route"] = fallback_route.as_dict()

            async def emit_fallback_handoff_failure(err: BaseException) -> None:
                error_details = {
                    "error_type": "deterministic_handoff_failed",
                    "retries": 3,
                    "message": str(err),
                    "error_class": err.__class__.__name__,
                    "recovery_suggestion": "escalating to human",
                    "fallback_target": fallback_route.target,
                }
                escalation = await self._escalate_to_human(
                    session, inbound.content, error_details
                )
                await self._emit_coo_error(
                    session=session,
                    session_id=session.key,
                    surface=inbound.channel,
                    error=err,
                    error_type="deterministic_handoff_failed",
                    retries=3,
                    user_message=inbound.content,
                    recovery_suggestion="escalating to human",
                    escalate_to_human=True,
                    approval_task_id=escalation.task_id,
                )

            try:
                result = await self._call_with_retry(
                    lambda: self.route_to_department(
                        session, fallback_route.target, payload
                    ),
                    on_final_failure=emit_fallback_handoff_failure,
                )
            except Exception as err:
                result = {
                    "session_id": session.key,
                    "task_id": payload.get("task_id"),
                    "target": fallback_route.target,
                    "status": "pending_human_escalation",
                    "route": fallback_route.as_dict(),
                    "deliverable": None,
                    "result": {
                        "summary": "COO escalated to a human after retry exhaustion.",
                        "content": str(err),
                        "artifact_type": "coo_escalation",
                        "metadata": session.metadata.get("last_human_escalation", {}),
                        "department": "coo",
                    },
                }
        result["action"] = session.metadata.get("last_coo_action", {})
        result["events"] = [event.as_dict() for event in self.bus.events]
        result["bus"] = self.bus.snapshot()
        return result

    async def decide_action(
        self,
        prompt: str,
        session: COOSession,
        *,
        target: str | None = None,
    ) -> COOActionDecision:
        """Choose the COO's CEO-facing action before any department routing."""
        if target:
            route = COORouteDecision(
                target=target, strategy="explicit", reason="Caller provided target"
            )
            return COOActionDecision(
                action="delegate_company_task",
                company_target=route.target,
                route=route,
                confidence=1.0,
                reasoning=route.reason,
            )

        deterministic = self._deterministic_personal_action(prompt, session)
        if deterministic is not None:
            return deterministic

        if self.enable_llm_routing and self.coo_agent_loop is not None:
            decision = await self._llm_action(prompt, session)
            if decision is not None:
                return decision

        route = self._deterministic_route(prompt)
        return COOActionDecision(
            action="delegate_company_task",
            company_target=route.target,
            route=route,
            confidence=route.confidence,
            reasoning=route.reason,
        )

    def _deterministic_personal_action(
        self, prompt: str, session: COOSession
    ) -> COOActionDecision | None:
        """Fast local personal-assistant actions that should not become tasks."""
        text = prompt.strip()
        prompt_lower = text.lower()
        if not text:
            return COOActionDecision(
                action="ask_ceo_clarification",
                response_to_ceo="CEO, what would you like me to operate on next?",
                confidence=1.0,
                reasoning="Empty message needs CEO clarification.",
            )
        if prompt_lower.startswith(("remember ", "note ", "preference:")):
            content = text.split(" ", 1)[1] if " " in text else text
            return COOActionDecision(
                action="update_memory",
                response_to_ceo=f"Noted, CEO. I will remember: {content}",
                confidence=0.95,
                memory_updates=[{"type": "ceo_preference", "content": content}],
                reasoning="CEO asked the COO to remember a preference or note.",
            )
        if any(
            phrase in prompt_lower
            for phrase in (
                "last deployment",
                "last deploy",
                "latest deployment",
                "recent deployment",
            )
        ):
            return COOActionDecision(
                action="inspect_status",
                response_to_ceo=self._format_last_deployment_for_ceo(),
                confidence=0.94,
                reasoning="CEO asked for the last deployment record.",
            )
        if any(
            phrase in prompt_lower
            for phrase in (
                "company status",
                "coo status",
                "status update",
                "ceo briefing",
                "operating brief",
                "pending approval",
                "pending approvals",
            )
        ):
            return COOActionDecision(
                action="inspect_status",
                response_to_ceo=self._format_company_status_for_ceo(session),
                confidence=0.9,
                reasoning="CEO requested status instead of new execution work.",
            )
        if any(
            phrase in prompt_lower
            for phrase in (
                "current vulnerabilities",
                "current vulnerability",
                "what are our vulnerabilities",
                "show open vulnerabilities",
                "open vulnerabilities",
                "security status",
                "latest security",
            )
        ):
            return COOActionDecision(
                action="inspect_status",
                response_to_ceo=self._format_security_status_for_ceo(session),
                confidence=0.94,
                reasoning="CEO asked for current security posture or vulnerabilities.",
            )
        if any(
            phrase in prompt_lower
            for phrase in (
                "what skills",
                "recall a skill",
                "recall skill",
                "inspect skills",
                "learned skills",
                "available skills",
            )
        ):
            return COOActionDecision(
                action="inspect_skills",
                response_to_ceo=self._format_skills_for_ceo(),
                confidence=0.92,
                reasoning="CEO asked about procedural skills.",
            )
        if any(
            phrase in prompt_lower
            for phrase in (
                "why did you use that skill",
                "why did you use the skill",
                "why did you use that memory",
                "why was that skill used",
                "why was this skill used",
                "why was that included",
            )
        ):
            return COOActionDecision(
                action="inspect_knowledge",
                response_to_ceo=self._format_retrieval_explanations_for_ceo(),
                confidence=0.94,
                reasoning="CEO asked why knowledge or skills were injected.",
            )
        if any(
            phrase in prompt_lower
            for phrase in (
                "what knowledge",
                "inspect knowledge",
                "knowledge overview",
                "show recent playbook",
                "recent playbook items",
                "review pending skill proposals",
                "pending skill proposals",
                "skill proposals",
            )
        ):
            return COOActionDecision(
                action="inspect_knowledge",
                response_to_ceo=self._format_knowledge_for_ceo(query=text),
                confidence=0.93,
                reasoning="CEO asked to inspect institutional knowledge.",
            )
        if prompt_lower.startswith(("search knowledge", "find knowledge")):
            return COOActionDecision(
                action="search_knowledge",
                response_to_ceo=self._format_knowledge_for_ceo(query=text),
                confidence=0.92,
                reasoning="CEO asked to search institutional knowledge.",
            )
        if any(
            phrase in prompt_lower
            for phrase in (
                "daemon status",
                "daemon workflow",
                "daemon workflows",
                "proof-of-work",
                "proof of work",
            )
        ):
            return COOActionDecision(
                action="list_daemon_workflows",
                response_to_ceo=self._format_daemon_workflows_for_ceo(),
                confidence=0.92,
                reasoning="CEO asked about daemon workflow state.",
            )
        if any(
            phrase in prompt_lower
            for phrase in ("what do you remember", "recall memory", "coo memory")
        ):
            memory_items = self.recall_ceo_memory(limit=8)
            if memory_items:
                lines = ["CEO, here is what I remember:"]
                lines.extend(f"- {item.get('content', item)}" for item in memory_items)
                response = "\n".join(lines)
            else:
                response = "CEO, I do not have any COO memory notes yet."
            return COOActionDecision(
                action="recall_memory",
                response_to_ceo=response,
                confidence=0.9,
                reasoning="CEO asked to recall COO memory.",
            )
        return None

    async def _llm_action(
        self, prompt: str, session: COOSession
    ) -> COOActionDecision | None:
        task_payload = {
            "prompt": prompt,
            "history": session.get_history(12),
            "ceo_profile": self._read_coo_profile(),
            "coo_memory": self.recall_ceo_memory(limit=8),
            "company_status": self.inspect_company_status(session),
            "departments": sorted(self.orchestrator.departments),
            "skill_guidance": self._coo_skill_guidance(prompt),
        }
        surface = (
            session.messages[-1].get("surface") if session.messages else "coo"
        ) or "coo"

        async def emit_llm_action_failure(err: BaseException) -> None:
            await self._emit_coo_error(
                session=session,
                session_id=session.key,
                surface=surface,
                error=err,
                error_type="llm_route_failed",
                retries=3,
                user_message=prompt,
                recovery_suggestion="falling back to deterministic routing",
                escalate_to_human=False,
            )

        try:
            result = await self._call_with_retry(
                lambda: self.coo_agent_loop.run_structured(
                    task=json.dumps(task_payload, ensure_ascii=False),
                    system_prompt=self._get_coo_action_prompt(session),
                    enable_caching=self.agent_config.enable_caching,
                ),
                base_delay=0.01,
                on_final_failure=emit_llm_action_failure,
            )
        except Exception:
            return None

        parsed = self._parse_json(result.get("content", ""))
        return self._action_from_parsed(parsed)

    def _action_from_parsed(self, parsed: dict[str, Any]) -> COOActionDecision | None:
        if not parsed:
            return None
        candidate = (
            str(
                parsed.get("company_target")
                or parsed.get("chosen_department")
                or parsed.get("target")
                or ""
            )
            .strip()
            .lower()
        )
        action_name = parsed.get("action")
        if not action_name and candidate:
            action_name = "delegate_company_task"
        if not action_name:
            return None

        route = None
        if candidate:
            if candidate not in self.orchestrator.departments:
                return None
            route = COORouteDecision(
                target=candidate,
                strategy="llm",
                reason=str(parsed.get("reasoning") or parsed.get("reason") or ""),
                confidence=parsed.get("confidence", 0.5),
                should_escalate_to_human=bool(
                    parsed.get("should_escalate_to_human", False)
                ),
                escalate_to_human=bool(parsed.get("escalate_to_human", False)),
                metadata={"raw": parsed},
            )
        return COOActionDecision(
            action=action_name,
            response_to_ceo=str(
                parsed.get("response_to_ceo") or parsed.get("response") or ""
            ),
            confidence=parsed.get("confidence", 0.5),
            requires_approval=bool(parsed.get("requires_approval", False)),
            company_target=candidate or parsed.get("company_target"),
            tool_name=parsed.get("tool_name"),
            memory_updates=parsed.get("memory_updates", []) or [],
            context=parsed.get("context", {}) or {},
            route=route,
            reasoning=str(parsed.get("reasoning") or parsed.get("reason") or ""),
        )

    async def _complete_personal_action(
        self,
        session: COOSession,
        message: COOMessage,
        action: COOActionDecision,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if action.action == "inspect_status":
            content = action.response_to_ceo or self._format_company_status_for_ceo(
                session
            )
        elif action.action == "update_memory":
            for update in action.memory_updates:
                self.remember_ceo_preference(update)
            content = action.response_to_ceo or "Noted, CEO. I updated COO memory."
        elif action.action == "recall_memory":
            content = action.response_to_ceo
        elif action.action == "inspect_skills":
            content = action.response_to_ceo or self._format_skills_for_ceo()
        elif action.action == "inspect_knowledge":
            content = action.response_to_ceo or self._format_knowledge_for_ceo()
        elif action.action == "search_knowledge":
            content = action.response_to_ceo or self._format_knowledge_for_ceo(
                query=message.content
            )
        elif action.action == "list_daemon_workflows":
            content = action.response_to_ceo or self._format_daemon_workflows_for_ceo()
        elif action.action == "ask_ceo_clarification":
            content = (
                action.response_to_ceo or "CEO, can you clarify the desired outcome?"
            )
        elif action.action == "use_tool":
            content = (
                action.response_to_ceo
                or f"CEO, I identified `{action.tool_name or 'a tool'}` as the next tool, but this COO tool adapter is not wired yet."
            )
        else:
            content = action.response_to_ceo or "CEO, I am ready to help."

        session.add_message(
            "assistant",
            content,
            department="coo",
            status="success",
            task_id=payload.get("task_id"),
            coo_action=action.as_dict(),
        )
        session.metadata["last_deliverable_summary"] = content
        self.session_manager.save(session)
        await self.bus.publish_outbound(
            COOMessage(
                channel=message.channel,
                session_key=session.key,
                role="assistant",
                content=content,
                metadata={
                    "task_id": payload.get("task_id"),
                    "department": "coo",
                    "coo_action": action.as_dict(),
                },
            )
        )
        result = {
            "session_id": session.key,
            "task_id": payload.get("task_id"),
            "target": "coo",
            "status": "success",
            "route": None,
            "action": action.as_dict(),
            "deliverable": None,
            "result": {
                "summary": content,
                "content": content,
                "artifact_type": action.action,
                "metadata": {"coo_action": action.as_dict()},
                "department": "coo",
            },
        }
        result["events"] = [event.as_dict() for event in self.bus.events]
        result["bus"] = self.bus.snapshot()
        return result

    async def delegate_company_task(
        self,
        session: COOSession,
        message: COOMessage,
        route: COORouteDecision,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """COO tool: delegate CEO work into the internal company workflow."""
        return await self._delegate_with_route(session, message, route, payload)

    def inspect_company_status(self, session: COOSession) -> dict[str, Any]:
        """COO tool: return a structured CEO operating-status payload."""
        return self._company_status_payload(session)

    def get_pending_approvals(self) -> list[str]:
        """COO tool: list pending approval IDs that need CEO attention."""
        return sorted(self.orchestrator.approvals.gates)

    def remember_ceo_preference(self, item: dict[str, Any] | str) -> dict[str, Any]:
        """COO tool: persist a CEO preference or operational note."""
        payload = (
            {"type": "ceo_preference", "content": item}
            if isinstance(item, str)
            else dict(item)
        )
        self._append_coo_memory(payload)
        return payload

    def recall_ceo_memory(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """COO tool: recall recent repo-local COO memory notes."""
        return self._read_coo_memory(limit=limit)

    def inspect_skills(self) -> dict[str, Any]:
        """COO tool: inspect learned procedural skills and recent usage."""

        manager = CompanySkillManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        )
        return manager.inspect_skills()

    def inspect_knowledge(self, *, query: str = "") -> dict[str, Any]:
        """COO tool: inspect playbooks, skills, proposals, and COO memory."""

        return KnowledgeManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        ).get_overview(query=query)

    def search_knowledge(self, query: str) -> list[dict[str, Any]]:
        """COO tool: search learned institutional knowledge."""

        return KnowledgeManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        ).search_knowledge(query)


    def list_skills_needing_attention(self) -> dict[str, Any]:
        """COO personal action: list pending patch/retirement skill proposals."""

        km = KnowledgeManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        )
        return {
            "skills_needing_patch": km.get_skills_needing_patch(),
            "skills_needing_retirement": km.get_skills_needing_retirement(),
        }

    def explain_skill(self, skill_name: str) -> dict[str, Any]:
        """COO personal action: explain a skill/proposal with evidence."""

        km = KnowledgeManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        )
        for skill in km.get_all_skills():
            if skill.get("name") == skill_name or skill.get("id") == skill_name:
                return km.get_evidence_for_skill(str(skill.get("id")))
        for proposal in km.get_skill_proposals():
            if proposal.get("name") == skill_name or proposal.get("proposal_id") == skill_name:
                return km.get_evidence_for_skill(str(proposal.get("proposal_id")))
        return {"skill_name": skill_name, "status": "not_found"}

    def review_proposal(self, proposal_id: str, decision: str = "approve") -> dict[str, Any]:
        """COO personal action: approve/reject pending skill proposal."""

        km = KnowledgeManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        )
        if decision == "reject":
            return km.reject_skill_proposal(proposal_id)
        return km.approve_skill_proposal(proposal_id)

    def list_available_mcp_tools(self) -> list[dict[str, str]]:
        """COO personal action: list approval-aware MCP tools."""

        from aider.mcp.server import list_builtin_mcp_tools

        return list_builtin_mcp_tools()

    def explain_mcp_tool(self, tool_name: str) -> dict[str, str]:
        """COO personal action: explain an MCP tool and its approval policy."""

        for tool in self.list_available_mcp_tools():
            if tool["name"] == tool_name:
                return tool
        return {
            "name": tool_name,
            "permission_level": "unknown",
            "description": "No MCP tool with that name is registered.",
        }

    def list_daemon_workflows(self) -> dict[str, Any]:
        """COO tool: inspect repo-local daemon workflow status when configured."""

        workflow_path = Path(self.orchestrator.memory.repo_path) / "AIDER_WORKFLOW.md"
        if not workflow_path.exists():
            return {
                "configured": False,
                "workflow": str(workflow_path),
                "status": "not_configured",
                "workflows": [],
            }
        try:
            status = load_daemon(workflow_path).get_status()
        except (CompanyDaemonError, WorkflowError, OSError, ValueError) as exc:
            return {
                "configured": True,
                "workflow": str(workflow_path),
                "status": "unavailable",
                "error": str(exc),
                "workflows": [],
            }
        return {
            "configured": True,
            "workflow": str(workflow_path),
            "status": status.get("status", "unknown"),
            "workflows": [status],
        }

    def _coo_memory_dir(self) -> Path:
        path = Path(self.orchestrator.memory.repo_path) / ".aider" / "coo"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _coo_profile_path(self) -> Path:
        return self._coo_memory_dir() / "profile.json"

    def _coo_memory_path(self) -> Path:
        return self._coo_memory_dir() / "memory.jsonl"

    def _read_coo_profile(self) -> dict[str, Any]:
        path = self._coo_profile_path()
        if not path.exists():
            profile = {
                "role": "Chief Executive Officer",
                "coo_role": "Chief Operating Officer personal assistant",
                "communication_style": "executive, concise, action-oriented",
                "approval_preferences": {
                    "prd": "ask",
                    "release": "ask",
                    "risky_tools": "ask",
                },
            }
            path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return profile
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _append_coo_memory(self, item: dict[str, Any]) -> None:
        payload = {"created_at": datetime.utcnow().isoformat(), **dict(item)}
        if "content" not in payload:
            payload["content"] = json.dumps(item, ensure_ascii=False)
        with open(self._coo_memory_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + os.linesep)

    def _read_coo_memory(self, *, limit: int = 10) -> list[dict[str, Any]]:
        path = self._coo_memory_path()
        if not path.exists():
            return []
        items: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    items.append(payload)
        return items[-max(1, limit) :]

    def _company_status_payload(self, session: COOSession) -> dict[str, Any]:
        project = self.orchestrator.active_project
        pending_approvals = self.get_pending_approvals()
        delivery_plan = getattr(project, "delivery_plan", None)
        delivery_status = delivery_plan.to_summary() if delivery_plan else None
        return {
            "ceo_role": "Chief Executive Officer",
            "coo_role": "Chief Operating Officer",
            "active_project_phase": getattr(project, "phase", None) or "unassigned",
            "active_project_name": getattr(project, "name", None),
            "delivery_status": delivery_status,
            "departments": sorted(self.orchestrator.departments),
            "active_department": session.metadata.get("last_target"),
            "last_task_id": session.metadata.get("last_task_id"),
            "last_route": session.metadata.get("last_route", {}),
            "pending_approvals": pending_approvals,
            "recent_errors": list(session.metadata.get("recent_errors", []) or [])[-5:],
            "message_count": len(session.messages),
            "skills_summary": self._skills_status_summary(),
            "daemon": self.list_daemon_workflows(),
            "last_deployment": self._last_deployment_payload(),
            "security": self._security_status_payload(),
        }

    def _last_deployment_payload(self) -> dict[str, Any] | None:
        project = self.orchestrator.active_project
        deploy_result = getattr(project, "deploy_result", None) if project else None
        if not deploy_result or not isinstance(
            getattr(deploy_result, "payload", None), dict
        ):
            return None
        payload = deploy_result.payload
        build = payload.get("build_artifact") or {}
        deployment = payload.get("deployment_result") or {}
        metadata = getattr(deploy_result, "metadata", {}) or {}
        return {
            "task_id": getattr(deploy_result, "task_id", None),
            "status": getattr(deploy_result, "status", None),
            "summary": payload.get("summary"),
            "environment": payload.get("environment") or deployment.get("environment"),
            "deploy_url": (
                payload.get("deploy_url")
                or deployment.get("deployed_url")
                or metadata.get("deploy_url")
            ),
            "git_tag": payload.get("git_tag")
            or build.get("tag")
            or metadata.get("git_tag"),
            "artifact_location": payload.get("release_artifact")
            or build.get("location"),
            "artifact_type": build.get("artifact_type"),
            "logs_summary": (
                payload.get("deployment_logs_summary")
                or deployment.get("deployment_logs")
            ),
            "log_artifacts": payload.get("log_artifacts")
            or metadata.get("log_artifacts")
            or [],
        }

    def _format_last_deployment_for_ceo(self) -> str:
        deployment = self._last_deployment_payload()
        if not deployment:
            return "CEO, I do not have a recorded deployment for this project yet."
        lines = [
            "CEO, the last deployment I have on record is:",
            f"- Status: {deployment.get('status') or 'unknown'}",
            f"- Environment: {deployment.get('environment') or 'unknown'}",
            f"- Artifact: {deployment.get('artifact_location') or 'unknown'}",
            f"- Git tag: {deployment.get('git_tag') or 'untagged'}",
            f"- URL: {deployment.get('deploy_url') or 'n/a'}",
        ]
        if deployment.get("logs_summary"):
            lines.append(f"- Logs: {str(deployment['logs_summary'])[:400]}")
        artifacts = deployment.get("log_artifacts") or []
        if artifacts:
            lines.append("- Log artifacts: " + ", ".join(map(str, artifacts[:3])))
        return "\n".join(lines)

    def _skills_status_summary(self) -> dict[str, Any]:
        skills = self.inspect_skills()
        pending = skills.get("pending_proposals", [])
        patch_needed = len(
            [
                p
                for p in pending
                if isinstance(p, dict) and p.get("action") == "patch"
            ]
        )
        return {
            "available_count": skills.get("available_count", 0),
            "recently_used_count": skills.get("recently_used_count", 0),
            "recently_used": skills.get("recently_used", [])[:3],
            "available": skills.get("available", [])[:5],
            "pending_proposals": len(pending),
            "skills_needing_patch": patch_needed,
            "retired_skills_count": skills.get("retired_skills_count", 0),
        }

    def _format_skills_for_ceo(self) -> str:
        skills = self.inspect_skills()
        lines = [
            "CEO, here are the learned procedural skills:",
            f"- Available: {skills.get('available_count', 0)}",
            f"- Recently used: {skills.get('recently_used_count', 0)}",
            f"- Pending proposals: {len(skills.get('pending_proposals', []))}",
        ]
        pending = skills.get("pending_proposals", [])
        patch_needed = len(
            [
                p
                for p in pending
                if isinstance(p, dict) and p.get("action") == "patch"
            ]
        )
        lines.append(
            f"- Skill health: {patch_needed} skills need patching, {skills.get('retired_skills_count', 0)} retired."
        )
        recent = skills.get("recently_used") or []
        available = skills.get("available") or []
        if recent:
            lines.append("- Recent skills:")
            lines.extend(
                f"  - {item.get('scope')}/{item.get('name')}: "
                f"{item.get('title') or item.get('description') or 'Untitled'}"
                for item in recent[:5]
            )
        if available:
            lines.append("- Available skills:")
            lines.extend(
                f"  - {item.get('scope')}/{item.get('name')}: "
                f"{item.get('description') or item.get('title') or 'No summary'}"
                for item in available[:5]
            )
        return "\n".join(lines)

    def _format_retrieval_explanations_for_ceo(self) -> str:
        overview = self.inspect_knowledge()
        recent = overview.get("recently_injected") or []
        lines = ["CEO, here is why recent skills or memories were included:"]
        if not recent:
            lines.append("- No knowledge has been injected in this session yet.")
            return "\n".join(lines)
        for item in recent[:5]:
            lines.append(f"- {item.get('explanation', 'No explanation recorded.')}")
        return "\n".join(lines)

    def _format_knowledge_for_ceo(self, *, query: str = "") -> str:
        overview = self.inspect_knowledge(query=query)
        counts = overview.get("counts", {})
        lines = [
            "CEO, here is the institutional knowledge overview:",
            f"- Playbook items: {counts.get('playbooks', 0)}",
            f"- Approved skills: {counts.get('skills', 0)}",
            f"- Pending skill proposals: {counts.get('pending_proposals', 0)}",
            f"- Skill health: {counts.get('skills_needing_patch', 0)} need patching, {counts.get('retired_skills_archive', 0)} retired.",
            f"- COO memory entries: {counts.get('coo_memory_entries', 0)}",
        ]
        recent_injected = overview.get("recently_injected") or []
        if recent_injected:
            lines.append("- Recently injected knowledge:")
            for item in recent_injected[:5]:
                lines.append(f"  - {item.get('explanation', '')}")
        if query and overview.get("search_results"):
            lines.append("- Matching knowledge:")
            for item in overview.get("search_results", [])[:5]:
                label = (
                    item.get("title")
                    or item.get("name")
                    or item.get("proposal_id")
                    or item.get("id")
                )
                lines.append(f"  - {item.get('type', 'knowledge')}: {label}")
        pending = overview.get("pending_proposals") or []
        if pending:
            lines.append("- Pending proposals to review:")
            for proposal in pending[:5]:
                lines.append(
                    f"  - {proposal.get('proposal_id')}: {proposal.get('scope')}/{proposal.get('name')} — {proposal.get('title')}"
                )
        retired = overview.get("retired_skills_archive") or []
        if retired:
            lines.append("- Retired skills archive (collapsed preview):")
            for item in retired[:3]:
                lines.append(
                    f"  - {item.get('scope')}/{item.get('name')} — {item.get('retirement_reason') or 'No reason recorded'}"
                )
        playbooks = overview.get("playbooks") or []
        if playbooks:
            lines.append("- Recent playbook items:")
            for item in playbooks[-5:]:
                lines.append(
                    f"  - {item.get('category')}: {item.get('summary', '')[:120]}"
                )
        return "\n".join(lines)

    def _format_daemon_workflows_for_ceo(self) -> str:
        daemon = self.list_daemon_workflows()
        if not daemon.get("configured"):
            return (
                "CEO, no AIDER_WORKFLOW.md daemon workflow is configured for this repo."
            )
        if daemon.get("status") == "unavailable":
            return f"CEO, daemon status is unavailable: {daemon.get('error')}"
        workflows = daemon.get("workflows") or []
        if not workflows:
            return "CEO, no daemon workflows are available."
        lines = ["CEO, daemon workflow status:"]
        for workflow in workflows:
            lines.extend(
                [
                    f"- Workflow: {workflow.get('workflow')}",
                    f"  Status: {workflow.get('status', 'unknown')}",
                    f"  Last run: {workflow.get('last_run') or 'never'}",
                    f"  Active workflows: {workflow.get('active_workflows', 0)}",
                    f"  Pending proof-of-work: {workflow.get('pending_proof_of_work', 0)}",
                ]
            )
        return "\n".join(lines)

    def _format_company_status_for_ceo(self, session: COOSession) -> str:
        status = self._company_status_payload(session)
        lines = [
            "CEO briefing:",
            f"- Project phase: {status['active_project_phase']}",
            f"- Departments online: {', '.join(status['departments']) or 'none'}",
            f"- Active department: {status['active_department'] or 'none'}",
            f"- Last task: {status['last_task_id'] or 'none'}",
            f"- Pending CEO approvals: {len(status['pending_approvals'])}",
            self._format_delivery_status_line(status.get("delivery_status")),
            f"- Skills: {status['skills_summary'].get('available_count', 0)} available / "
            f"{status['skills_summary'].get('recently_used_count', 0)} recently used",
            f"- Daemon: {status['daemon'].get('status', 'not_configured')}",
            self._format_security_status_line(status.get("security")),
            self._format_last_deployment_status_line(status.get("last_deployment")),
        ]
        if status.get("delivery_status"):
            delivery = status["delivery_status"]
            blockers = delivery.get("critical_blockers") or []
            lines.append(f"  - Next milestone: {delivery.get('next_milestone', 'TBD')}")
            lines.append(
                "  - Critical blockers: "
                + (", ".join(map(str, blockers)) if blockers else "none")
            )
        if status["pending_approvals"]:
            lines.append("  - " + ", ".join(status["pending_approvals"]))
        if status["recent_errors"]:
            lines.append(f"- Recent COO errors: {len(status['recent_errors'])}")
        return "\n".join(lines)

    def _security_status_payload(self) -> dict[str, Any]:
        data = getattr(self.orchestrator.memory, "data", {})
        security = data.get("security", {}) if isinstance(data, dict) else {}
        if not isinstance(security, dict) or not security:
            return {"status": "not_scanned", "severity": "info", "finding_count": 0}
        return dict(security)

    @staticmethod
    def _format_security_status_line(security: dict[str, Any] | None) -> str:
        if not security or security.get("status") == "not_scanned":
            return "- Security: not scanned"
        return (
            "- Security: "
            f"{security.get('posture') or str(security.get('status', 'unknown')).upper()} "
            f"({security.get('severity', 'info')} {security.get('scan_type') or 'scan'}, "
            f"{security.get('finding_count', 0)} finding(s); "
            f"last scan {security.get('last_scan_at') or 'never'}, "
            f"next {security.get('next_scan_at') or 'unscheduled'})"
        )

    def _format_security_status_for_ceo(self, session: COOSession) -> str:
        security = self._company_status_payload(session).get("security") or {}
        lines = [
            "CEO, current security posture:",
            self._format_security_status_line(security),
        ]
        result = security.get("result") if isinstance(security, dict) else None
        findings = result.get("findings", []) if isinstance(result, dict) else []
        lines.append(f"- Last scan: {security.get('last_scan_at') or 'never'}")
        lines.append(
            f"- Next scheduled scan: {security.get('next_scan_at') or 'unscheduled'}"
        )
        lines.append(
            f"- Open critical/high: {security.get('open_critical_count', 0)}/{security.get('open_high_count', 0)}"
        )
        recent_patches = (
            security.get("recent_patches_applied") if isinstance(security, dict) else []
        )
        if recent_patches:
            lines.append("- Recent patches applied:")
            for patch in recent_patches[-3:]:
                lines.append(
                    f"  - {patch.get('finding_id') or patch.get('task_id')}: {patch.get('status')}"
                )
        if findings:
            lines.append("- Current vulnerabilities:")
            for finding in findings[:5]:
                if not isinstance(finding, dict):
                    lines.append(f"  - {finding}")
                    continue
                lines.append(
                    "  - "
                    f"{finding.get('id') or finding.get('location') or 'finding'}: "
                    f"{finding.get('description') or finding.get('recommendation') or 'No details'}"
                )
        else:
            lines.append("- Current vulnerabilities: none recorded")
        return "\n".join(lines)

    @staticmethod
    def _format_last_deployment_status_line(deployment: dict[str, Any] | None) -> str:
        if not deployment:
            return "- Last deployment: none recorded"
        return (
            "- Last deployment: "
            f"{deployment.get('status') or 'unknown'} to {deployment.get('environment') or 'unknown'} "
            f"({deployment.get('git_tag') or 'untagged'})"
        )

    @staticmethod
    def _format_delivery_status_line(delivery_status: dict[str, Any] | None) -> str:
        if not delivery_status:
            return "- Delivery: no active plan yet"
        completion = delivery_status.get(
            "weighted_completion", delivery_status.get("completion_percentage", 0)
        )
        blockers = delivery_status.get("critical_blockers") or []
        blocker_note = (
            f", {len(blockers)} critical blocker(s)"
            if blockers
            else ", no critical blockers"
        )
        return (
            f"- Delivery: {delivery_status.get('status') or delivery_status.get('overall_status')} "
            f"at {completion}%{blocker_note}"
        )

    def _get_coo_action_prompt(self, session: COOSession) -> str:
        departments = sorted(self.orchestrator.departments)
        return (
            "You are NanobotCOO, the Chief Operating Officer and persistent personal "
            "assistant for the human Chief Executive Officer (CEO). Your job is to "
            "understand the CEO's intent, answer directly when no delegation is needed, "
            "ask clarifying questions when the objective is unclear, remember durable "
            "CEO preferences, inspect company status, use approved tools, or delegate "
            "work into the existing Aider Plus CompanyOrchestrator.\n\n"
            "Use available Procedural Skills from the task payload when they match "
            "the CEO's request; they are lightweight operating procedures, not "
            "extra departments. "
            "Do not replace Product, UX, Engineering, QA, Delivery, DevOps, or the "
            "CompanyOrchestrator. When execution belongs to the internal company, choose "
            "action=delegate_company_task and select exactly one company_target from the "
            f"available departments: {', '.join(departments)}.\n"
            "When a direct executive response is enough, choose answer_directly. "
            "When the CEO asks about status or approvals, choose inspect_status. "
            "When the CEO asks about learned procedural skills, choose inspect_skills. When the CEO asks about institutional knowledge, playbooks, skill proposals, or why agents behave a certain way, choose inspect_knowledge or search_knowledge. "
            "When the CEO asks about daemon workflows or proof-of-work, choose list_daemon_workflows. "
            "When the CEO asks you to remember something, choose update_memory. "
            "When the CEO asks what you remember, choose recall_memory. "
            "When unsafe or ambiguous, choose ask_ceo_clarification.\n\n"
            "Return only JSON matching this COOActionDecision schema:\n"
            "{\n"
            '  "action": "answer_directly | ask_ceo_clarification | delegate_company_task | inspect_status | inspect_skills | inspect_knowledge | search_knowledge | list_daemon_workflows | update_memory | recall_memory | use_tool",\n'
            '  "response_to_ceo": "short executive response when not delegating",\n'
            '  "company_target": "one available department when delegating, otherwise null",\n'
            '  "tool_name": "optional tool name",\n'
            '  "confidence": 0.0,\n'
            '  "requires_approval": false,\n'
            '  "memory_updates": [{"type": "ceo_preference", "content": "..."}],\n'
            '  "reasoning": "short explanation"\n'
            "}\n\n"
            f"Current company status: {json.dumps(self._company_status_payload(session), ensure_ascii=False)}"
        )

    def _coo_skill_guidance(self, prompt: str) -> list[str]:
        if not self.orchestrator.company_config.skill_learning.enabled:
            return []
        task = CompanyTask(
            task_id="coo-skill-query",
            origin="ceo",
            target="coo",
            artifact_type="general",
            payload=prompt,
        )
        manager = CompanySkillManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        )
        return manager.format_skill_guidance(manager.query_for_task(task, role="coo"))

    async def get_session_status(self, session_id: str) -> dict[str, Any]:
        """Return a clean dashboard payload for COO observability surfaces."""
        session = self.session_manager.get_or_create(session_id)
        session_snapshot = session.snapshot()
        bus_snapshot = self.bus.snapshot()
        return {
            "session_id": session_id,
            "status": "active" if session.messages else "new",
            "active_department": session_snapshot.get("active_department"),
            "current_route": session.metadata.get("last_route", {}),
            "last_coo_action": session.metadata.get("last_coo_action", {}),
            "ceo_profile": self._read_coo_profile(),
            "coo_memory": self.recall_ceo_memory(limit=5),
            "last_deliverable_summary": session_snapshot.get(
                "last_deliverable_summary"
            ),
            "last_deployment": self._last_deployment_payload(),
            "recent_events": bus_snapshot.get("formatted_events", []),
            "session": session_snapshot,
            "route_history": session_snapshot.get("route_history", []),
            "error_count": session_snapshot.get("error_count", 0),
            "last_error": session_snapshot.get("last_error"),
            "recent_errors": session_snapshot.get("recent_errors", []),
            "pending_human_escalations": session_snapshot.get(
                "pending_human_escalations", []
            ),
            "last_human_escalation": session_snapshot.get("last_human_escalation"),
            "skills_summary": self._skills_status_summary(),
            "daemon": self.list_daemon_workflows(),
            "security_card": self._security_status_payload(),
            "attention": {
                "has_recent_errors": bool(session_snapshot.get("recent_errors")),
                "has_pending_human_escalations": bool(
                    session_snapshot.get("pending_human_escalations")
                ),
            },
            "metrics": {
                "message_count": session_snapshot.get("message_count", 0),
                "inbound_queue_size": bus_snapshot.get("inbound_size", 0),
                "outbound_queue_size": bus_snapshot.get("outbound_size", 0),
                "error_count": session_snapshot.get("error_count", 0),
                **bus_snapshot.get("stats", {}),
            },
            "bus": bus_snapshot,
        }

    async def route_to_department(
        self,
        session: COOSession,
        department_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Create and submit/run a department task for an already-routed session."""
        target = department_name.lower()
        if target not in self.orchestrator.departments:
            raise ValueError(f"No department: {target}")

        task = CompanyTask(
            task_id=payload.get("task_id") or str(uuid.uuid4()),
            origin=payload.get("origin") or payload.get("surface") or "coo",
            target=target,
            artifact_type=payload.get("artifact_type", "raw_prompt"),
            payload=payload.get("message"),
            blocking=payload.get("blocking", False),
            context=payload.get("context") or {},
        )
        session.metadata.update(
            {
                "last_task_id": task.task_id,
                "last_target": target,
                "last_surface": payload.get("surface"),
                "last_route": payload.get("route", {}),
            }
        )
        self.session_manager.save(session)

        communication_memory.route_decision(
            self.orchestrator.memory,
            task=task,
            target=target,
            strategy="coo_route",
            reason="COO routed user message",
        )
        communication_memory.handoff(
            self.orchestrator.memory, task, source=task.origin, reason="coo_route"
        )

        if not payload.get("wait", True):
            await self.orchestrator.submit(task)
            self.session_manager.save(session)
            return {
                "session_id": session.key,
                "task_id": task.task_id,
                "target": target,
                "status": "submitted",
                "route": payload.get("route", {}),
                "deliverable": None,
                "result": None,
            }

        deliverable = await self._run_department_task(task)
        content = self._summarize_deliverable(deliverable)
        session.add_message(
            "assistant",
            content,
            department=deliverable.department,
            status=deliverable.status,
            task_id=deliverable.task_id,
        )
        self.session_manager.save(session)
        await self.bus.publish_outbound(
            COOMessage(
                channel=payload.get("surface") or "coo",
                session_key=session.key,
                role="assistant",
                content=content,
                metadata={
                    "task_id": task.task_id,
                    "department": deliverable.department,
                    "route": payload.get("route", {}),
                },
            )
        )
        return {
            "session_id": session.key,
            "task_id": task.task_id,
            "target": target,
            "status": deliverable.status,
            "route": payload.get("route", {}),
            "deliverable": deliverable,
            "result": {
                "summary": deliverable.payload,
                "content": deliverable.payload,
                "artifact_type": deliverable.artifact_type,
                "metadata": deliverable.metadata,
                "department": deliverable.department,
            },
        }

    async def _run_department_task(self, task: CompanyTask) -> Deliverable:
        """Run one department synchronously and route the produced deliverable."""
        department = self.orchestrator.departments[task.target]
        task.context = self.orchestrator.context_builder.build(
            task,
            department.get_context_requirements(),
            self.orchestrator.active_project,
        )
        deliverable = await department.process(task)
        await self.orchestrator._route(deliverable)
        return deliverable

    async def run_department_task(self, task: CompanyTask) -> Deliverable:
        """Backward-compatible wrapper around route_to_department for direct tasks."""
        session = self.session_manager.get_or_create(f"task:{task.task_id}")
        result = await self.route_to_department(
            session,
            task.target,
            {
                "message": task.payload,
                "surface": task.origin,
                "artifact_type": task.artifact_type,
                "context": task.context,
                "blocking": task.blocking,
                "wait": True,
                "task_id": task.task_id,
                "origin": task.origin,
                "route": {"target": task.target, "strategy": "direct"},
            },
        )
        return result["deliverable"]

    async def decide_route(self, prompt: str, session: COOSession) -> COORouteDecision:
        """Choose the target department using explicit LLM or deterministic strategies."""
        if self.enable_llm_routing and self.coo_agent_loop is not None:
            decision = await self._llm_route(prompt, session)
            if decision is not None:
                return decision
        return self._deterministic_route(prompt)

    async def _llm_route(
        self, prompt: str, session: COOSession
    ) -> COORouteDecision | None:
        task_payload = {
            "prompt": prompt,
            "history": session.get_history(12),
            "project_phase": self._current_project_phase(),
            "recent_route_history": self._recent_route_history(session, limit=3),
            "active_department": session.metadata.get("last_target"),
            "departments": sorted(self.orchestrator.departments),
            "skill_guidance": self._coo_skill_guidance(prompt),
        }
        surface = (
            session.messages[-1].get("surface") if session.messages else "coo"
        ) or "coo"

        async def emit_llm_route_failure(err: BaseException) -> None:
            await self._emit_coo_error(
                session=session,
                session_id=session.key,
                surface=surface,
                error=err,
                error_type="llm_route_failed",
                retries=3,
                user_message=prompt,
                recovery_suggestion="falling back to deterministic routing",
                escalate_to_human=False,
            )

        try:
            result = await self._call_with_retry(
                lambda: self.coo_agent_loop.run_structured(
                    task=json.dumps(task_payload, ensure_ascii=False),
                    system_prompt=self._get_coo_routing_prompt(session),
                    enable_caching=self.agent_config.enable_caching,
                ),
                base_delay=0.01,
                on_final_failure=emit_llm_route_failure,
            )
        except Exception:
            return None

        parsed = self._parse_json(result.get("content", ""))
        candidate = (
            str(parsed.get("chosen_department") or parsed.get("target") or "")
            .strip()
            .lower()
        )
        if candidate in self.orchestrator.departments:
            return COORouteDecision(
                target=candidate,
                strategy="llm",
                reason=str(parsed.get("reasoning") or parsed.get("reason") or ""),
                confidence=parsed.get("confidence", 0.5),
                should_escalate_to_human=bool(
                    parsed.get("should_escalate_to_human", False)
                ),
                escalate_to_human=bool(parsed.get("escalate_to_human", False)),
                metadata={"raw": parsed},
            )
        return None

    def _current_project_phase(self) -> str:
        project = self.orchestrator.active_project
        return getattr(project, "phase", None) or "unassigned"

    def _recent_route_history(
        self, session: COOSession, *, limit: int = 3
    ) -> list[dict[str, Any]]:
        routes = []
        for message in session.messages:
            route = message.get("route") or message.get("metadata", {}).get("route")
            if route:
                routes.append(route)
        last_route = session.metadata.get("last_route")
        if last_route and (not routes or routes[-1] != last_route):
            routes.append(last_route)
        return routes[-max(1, limit) :]

    def _get_coo_routing_prompt(self, session: COOSession) -> str:
        departments = sorted(self.orchestrator.departments)
        route_history = self._recent_route_history(session, limit=3)
        few_shots = [
            {
                "user": "Write regression tests for the retry logic and verify CI passes.",
                "decision": {
                    "reasoning": "The request is explicitly about tests and quality gates.",
                    "chosen_department": "qa",
                    "confidence": 0.91,
                    "should_escalate_to_human": False,
                    "escalate_to_human": False,
                },
            },
            {
                "user": "Design an accessible onboarding wireframe before implementation.",
                "decision": {
                    "reasoning": "The work asks for UX design artifacts before code.",
                    "chosen_department": "ux",
                    "confidence": 0.88,
                    "should_escalate_to_human": False,
                    "escalate_to_human": False,
                },
            },
            {
                "user": "Ship this unclear change directly to production; skip review if needed.",
                "decision": {
                    "reasoning": "The request is ambiguous and asks to bypass safety gates.",
                    "chosen_department": "product",
                    "confidence": 0.42,
                    "should_escalate_to_human": True,
                    "escalate_to_human": True,
                },
            },
        ]
        return (
            "You are the NanobotCOO routing agent. Choose exactly one department "
            "for the user's next work item while preserving production reliability.\n\n"
            f"Current project phase: {self._current_project_phase()}\n"
            f"Recent route history (last 3 decisions): "
            f"{json.dumps(route_history, ensure_ascii=False)}\n"
            f"Active department: {session.metadata.get('last_target') or 'none'}\n"
            f"Available departments list: {', '.join(departments)}\n\n"
            "Decision rules:\n"
            "- Review skill_guidance in the payload; if a skill summary clearly "
            "matches, route to the department that can apply it.\n"
            "- Pick only a department from the available departments list.\n"
            "- Prefer continuity with the active department unless the prompt clearly "
            "belongs elsewhere.\n"
            "- Consider the current project phase and avoid skipping required product, "
            "design, QA, or deployment gates.\n"
            "- Route when the request has a clear owner and can proceed safely.\n"
            "- Escalate to a human when the request is ambiguous, unsafe, asks to "
            "bypass approvals, conflicts with recent routing history, or lacks enough "
            "information to choose a safe department. Still choose the safest temporary "
            "department for bookkeeping.\n\n"
            "Few-shot route decisions (match this quality and JSON shape):\n"
            f"{json.dumps(few_shots, ensure_ascii=False, indent=2)}\n\n"
            "Return only JSON matching this COORouteDecision schema:\n"
            "{\n"
            '  "reasoning": "short explanation for the routing decision",\n'
            '  "chosen_department": "one of the available departments",\n'
            '  "confidence": 0.0,\n'
            '  "should_escalate_to_human": false,\n'
            '  "escalate_to_human": false\n'
            "}"
        )

    def _deterministic_route(self, prompt: str) -> COORouteDecision:
        if self.orchestrator.active_project is not None:
            project = self.orchestrator.active_project
            if (
                project.phase == "prototyping"
                and not project.prd
                and "product" in self.orchestrator.departments
            ):
                return COORouteDecision(
                    target="product",
                    strategy="deterministic",
                    reason="Prototype project needs a PRD before implementation",
                )
        prompt_lower = prompt.lower()
        keyword_routes = (
            (
                "security_app",
                (
                    "run security scan",
                    "security scan",
                    "vulnerability scan",
                    "vuln scan",
                    "pentest",
                    "appsec",
                ),
            ),
            (
                "security_platform",
                (
                    "platform security",
                    "platform audit",
                    "prompt injection",
                    "tool policy",
                    "mcp sandbox",
                    "agent isolation",
                ),
            ),
            ("qa", ("test", "tests", "qa", "quality", "bug reproduction")),
            (
                "delivery",
                (
                    "delivery",
                    "project management",
                    "timeline",
                    "milestone",
                    "risk",
                    "blocker",
                    "coordination",
                ),
            ),
            ("devops", ("deploy", "deployment", "ci", "release", "docker")),
            ("ux", ("design", "ux", "ui", "wireframe", "accessibility")),
            ("product", ("prd", "requirements", "product", "spec")),
            ("engineering", ("code", "implement", "refactor", "fix", "engineering")),
        )
        for target, keywords in keyword_routes:
            if target in self.orchestrator.departments and any(
                word in prompt_lower for word in keywords
            ):
                return COORouteDecision(
                    target=target,
                    strategy="deterministic",
                    reason=f"Matched {target} routing keywords",
                )
        target = (
            "engineering"
            if "engineering" in self.orchestrator.departments
            else self.default_target
        )
        if target not in self.orchestrator.departments:
            target = next(iter(self.orchestrator.departments))
        return COORouteDecision(
            target=target,
            strategy="deterministic",
            reason="Default route",
        )

    async def resolve_target(self, prompt: str, session: COOSession) -> str:
        """Backward-compatible target-only route resolver."""
        return (await self.decide_route(prompt, session)).target

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        try:
            parsed = json.loads(content.strip())
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _summarize_deliverable(deliverable: Deliverable) -> str:
        if isinstance(deliverable.payload, str):
            return deliverable.payload
        return json.dumps(deliverable.payload, ensure_ascii=False, default=str)
