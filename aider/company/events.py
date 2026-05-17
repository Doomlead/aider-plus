"""Typed, shared runtime event stream for Company Mode surfaces."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

EVENT_VERSION = 1


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for event envelopes."""

    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CompanyEvent:
    """Versioned Company runtime event shared by all UI/API surfaces."""

    event_type: str
    timestamp: str = field(default_factory=utc_now)
    session_id: str = "company"
    payload: dict[str, Any] = field(default_factory=dict)
    version: int = EVENT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompanyEvent":
        event_type = str(data.get("event_type") or data.get("type") or "company_event")
        event_cls = EVENT_CLASSES.get(event_type, cls)
        return event_cls(
            event_type=event_type,
            timestamp=str(data.get("timestamp") or utc_now()),
            session_id=str(data.get("session_id") or "company"),
            payload=dict(data.get("payload") or {}),
            version=int(data.get("version") or EVENT_VERSION),
        )


@dataclass(frozen=True)
class LifecycleEvent(CompanyEvent):
    event_type: str = "lifecycle"


@dataclass(frozen=True)
class DaemonRunProgress(CompanyEvent):
    event_type: str = "daemon_run_progress"


@dataclass(frozen=True)
class SkillProposalUpdated(CompanyEvent):
    event_type: str = "skill_proposal_updated"


@dataclass(frozen=True)
class DeploymentCompleted(CompanyEvent):
    event_type: str = "deployment_completed"


@dataclass(frozen=True)
class CooActionTaken(CompanyEvent):
    event_type: str = "coo_action_taken"


@dataclass(frozen=True)
class ApprovalRequired(CompanyEvent):
    event_type: str = "approval_required"


@dataclass(frozen=True)
class ProjectBlocked(CompanyEvent):
    event_type: str = "project_blocked"


@dataclass(frozen=True)
class DepartmentEvent(CompanyEvent):
    event_type: str = "department_event"


EVENT_CLASSES: dict[str, type[CompanyEvent]] = {
    "lifecycle": LifecycleEvent,
    "daemon_run_progress": DaemonRunProgress,
    "skill_proposal_updated": SkillProposalUpdated,
    "deployment_completed": DeploymentCompleted,
    "deployment_complete": DeploymentCompleted,
    "coo_action_taken": CooActionTaken,
    "approval_required": ApprovalRequired,
    "approval_requested": ApprovalRequired,
    "project_blocked": ProjectBlocked,
    "department_event": DepartmentEvent,
}

EventHandler = Callable[[CompanyEvent], Any]


class EventBus:
    """In-process pub/sub bus with bounded replay for Company runtime events."""

    def __init__(self, history_limit: int = 200, session_id: str = "company"):
        self.history_limit = max(1, int(history_limit))
        self.session_id = session_id
        self._recent: deque[CompanyEvent] = deque(maxlen=self.history_limit)
        self._handlers: list[EventHandler] = []
        self._lock = threading.RLock()

    def publish(self, event: CompanyEvent) -> CompanyEvent:
        """Publish an event to current subscribers and retain it for replay."""

        if not isinstance(event, CompanyEvent):
            raise TypeError("EventBus.publish expects a CompanyEvent")
        with self._lock:
            self._recent.append(event)
            handlers = list(self._handlers)
        for handler in handlers:
            result = handler(event)
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)
                else:
                    loop.create_task(result)
        return event

    async def publish_async(self, event: CompanyEvent) -> CompanyEvent:
        """Publish an event and await coroutine subscribers."""

        if not isinstance(event, CompanyEvent):
            raise TypeError("EventBus.publish_async expects a CompanyEvent")
        with self._lock:
            self._recent.append(event)
            handlers = list(self._handlers)
        for handler in handlers:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        return event

    def subscribe(
        self, handler: EventHandler, *, replay: bool = False
    ) -> Callable[[], None]:
        """Subscribe a handler and return an unsubscribe callback."""

        with self._lock:
            self._handlers.append(handler)
            replay_events = list(self._recent) if replay else []
        for event in replay_events:
            result = handler(event)
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)
                else:
                    loop.create_task(result)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers:
                    self._handlers.remove(handler)

        return unsubscribe

    def get_recent(self, limit: int = 50) -> list[CompanyEvent]:
        """Return recent events in chronological order."""

        with self._lock:
            events = list(self._recent)
        return events[-max(0, int(limit)) :]

    def event_stream(self, limit: int = 50) -> Iterable[str]:
        """Yield server-sent-event lines for the recent replay buffer."""

        for event in self.get_recent(limit=limit):
            yield f"event: {event.event_type}\ndata: {event.to_json()}\n\n"


global_event_bus = EventBus()


def event_from_legacy_message(
    message: Any, *, session_id: str = "company"
) -> CompanyEvent:
    """Convert existing EventMessage/Deliverable/COO objects into typed bus events."""

    if isinstance(message, CompanyEvent):
        return message
    event_value = getattr(message, "event", None)
    task_id = getattr(message, "task_id", "")
    metadata = dict(getattr(message, "metadata", {}) or {})
    raw_payload = getattr(message, "payload", {}) if hasattr(message, "payload") else {}
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    if event_value is not None:
        raw_type = getattr(event_value, "value", event_value)
        event_type = str(payload.get("name") or raw_type or "company_event")
        if event_type == "daemon_run_progress":
            cls: type[CompanyEvent] = DaemonRunProgress
        else:
            cls = EVENT_CLASSES.get(
                event_type, LifecycleEvent if raw_type == "lifecycle" else CompanyEvent
            )
        payload.setdefault("task_id", task_id)
        if metadata:
            payload.setdefault("metadata", metadata)
        return cls(event_type=event_type, session_id=session_id, payload=payload)

    department = getattr(message, "department", None)
    if department is not None:
        event_type = (
            "deployment_completed" if department == "devops" else "department_event"
        )
        return EVENT_CLASSES.get(event_type, DepartmentEvent)(
            event_type=event_type,
            session_id=session_id,
            payload={
                "task_id": task_id,
                "department": department,
                "status": getattr(message, "status", None),
                "artifact_type": getattr(message, "artifact_type", None),
                "payload": getattr(message, "payload", None),
                "metadata": metadata,
            },
        )

    event_type = str(getattr(message, "event_type", "company_event"))
    return EVENT_CLASSES.get(event_type, CompanyEvent)(
        event_type=event_type,
        session_id=session_id,
        payload={"message": str(message)},
    )
