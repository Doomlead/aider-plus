from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyTask, Deliverable
from aider.memory import ProjectMemory


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

    def __init__(self, *, history_limit: int = 200):
        self.inbound: asyncio.Queue[COOMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[COOMessage] = asyncio.Queue()
        self.history_limit = history_limit
        self.events: list[COOMessageBusEvent] = []
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
class COORouteDecision:
    """Explicit COO routing result for a user message."""

    target: str = ""
    strategy: str = "deterministic"
    reason: str = "Route selected by COO"
    confidence: float = 0.5
    should_escalate_to_human: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    chosen_department: str | None = None
    reasoning: str | None = None

    def __post_init__(self) -> None:
        target = self.chosen_department if self.chosen_department is not None else self.target
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
        self.should_escalate_to_human = bool(self.should_escalate_to_human)
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
        self.bus = bus or COOMessageBus()
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
    ) -> None:
        preview = user_message.replace("\n", " ")[:160]
        error_payload = {
            "error_type": error_type,
            "retries": retries,
            "user_message_preview": preview,
            "message": str(error),
            "error_class": error.__class__.__name__,
        }
        if session is not None:
            session.metadata["error_count"] = (
                int(session.metadata.get("error_count", 0) or 0) + 1
            )
            session.metadata["last_error"] = error_payload
            self.session_manager.save(session)
        await self.bus.emit_error(
            session_key=session_id,
            channel=surface,
            metadata=error_payload,
        )

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

        route = (
            COORouteDecision(
                target=target, strategy="explicit", reason="Caller provided target"
            )
            if target
            else await self.decide_route(inbound.content, session)
        )

        session.messages[-1]["route"] = route.as_dict()
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
            "route": route.as_dict(),
        }
        try:
            result = await self._call_with_retry(
                lambda: self.route_to_department(session, route.target, payload)
            )
        except Exception as err:
            await self._emit_coo_error(
                session=session,
                session_id=session_id,
                surface=surface,
                error=err,
                error_type="department_handoff_failed",
                retries=3,
                user_message=inbound.content,
            )
            fallback_route = self._deterministic_route(inbound.content)
            fallback_route.strategy = "deterministic_fallback"
            fallback_route.metadata["fallback_from"] = route.as_dict()
            session.messages[-1]["route"] = fallback_route.as_dict()
            self.session_manager.save(session)
            payload["route"] = fallback_route.as_dict()
            result = await self.route_to_department(
                session, fallback_route.target, payload
            )
        result["events"] = [event.as_dict() for event in self.bus.events]
        result["bus"] = self.bus.snapshot()
        return result

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
            "last_deliverable_summary": session_snapshot.get(
                "last_deliverable_summary"
            ),
            "recent_events": bus_snapshot.get("formatted_events", []),
            "session": session_snapshot,
            "route_history": session_snapshot.get("route_history", []),
            "error_count": session_snapshot.get("error_count", 0),
            "last_error": session_snapshot.get("last_error"),
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
        }
        try:
            result = await self._call_with_retry(
                lambda: self.coo_agent_loop.run_structured(
                    task=json.dumps(task_payload, ensure_ascii=False),
                    system_prompt=self._get_coo_routing_prompt(session),
                    enable_caching=self.agent_config.enable_caching,
                ),
                base_delay=0.01,
            )
        except Exception as err:
            await self._emit_coo_error(
                session=session,
                session_id=session.key,
                surface=(
                    session.messages[-1].get("surface") if session.messages else "coo"
                )
                or "coo",
                error=err,
                error_type="llm_route_failed",
                retries=3,
                user_message=prompt,
            )
            return None

        parsed = self._parse_json(result.get("content", ""))
        candidate = str(
            parsed.get("chosen_department") or parsed.get("target") or ""
        ).strip().lower()
        if candidate in self.orchestrator.departments:
            return COORouteDecision(
                target=candidate,
                strategy="llm",
                reason=str(parsed.get("reasoning") or parsed.get("reason") or ""),
                confidence=parsed.get("confidence", 0.5),
                should_escalate_to_human=bool(
                    parsed.get("should_escalate_to_human", False)
                ),
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
        return (
            "You are the NanobotCOO routing agent. Choose exactly one department "
            "for the user's next work item while preserving production reliability.\n\n"
            f"Current project phase: {self._current_project_phase()}\n"
            f"Recent route history (last 3 decisions): "
            f"{json.dumps(route_history, ensure_ascii=False)}\n"
            f"Active department: {session.metadata.get('last_target') or 'none'}\n"
            f"Available departments list: {', '.join(departments)}\n\n"
            "Decision rules:\n"
            "- Pick only a department from the available departments list.\n"
            "- Prefer continuity with the active department unless the prompt clearly "
            "belongs elsewhere.\n"
            "- Consider the current project phase and avoid skipping required product, "
            "design, QA, or deployment gates.\n"
            "- Escalate to a human only when routing is ambiguous, unsafe, or blocked.\n\n"
            "Return only JSON matching this COORouteDecision schema:\n"
            "{\n"
            '  "reasoning": "short explanation for the routing decision",\n'
            '  "chosen_department": "one of the available departments",\n'
            '  "confidence": 0.0,\n'
            '  "should_escalate_to_human": false\n'
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
            ("qa", ("test", "tests", "qa", "quality", "bug reproduction")),
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
