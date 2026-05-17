"""Typed, shared runtime event stream for Company Mode surfaces."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Literal

EVENT_VERSION = 1
SUPPORTED_EVENT_VERSIONS = (1,)
DEPRECATED_EVENT_VERSIONS: tuple[int, ...] = ()
EVENT_VERSION_DEPRECATIONS: dict[int, str] = {}
EventSeverity = Literal["info", "warning", "error"]
VALID_EVENT_SEVERITIES = {"info", "warning", "error"}


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
    severity: EventSeverity = "info"
    version: int = EVENT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "severity", normalize_severity(self.severity))

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
            severity=normalize_severity(data.get("severity", "info")),
            version=normalize_event_version(data.get("version")),
        )

    @property
    def is_deprecated(self) -> bool:
        return self.version in DEPRECATED_EVENT_VERSIONS

    @property
    def deprecation_message(self) -> str | None:
        return EVENT_VERSION_DEPRECATIONS.get(self.version)


def normalize_severity(value: Any) -> EventSeverity:
    severity = str(value or "info").lower()
    if severity not in VALID_EVENT_SEVERITIES:
        return "info"
    return severity  # type: ignore[return-value]


def normalize_event_version(value: Any) -> int:
    try:
        version = int(value or EVENT_VERSION)
    except (TypeError, ValueError):
        return EVENT_VERSION
    if version in SUPPORTED_EVENT_VERSIONS or version in DEPRECATED_EVENT_VERSIONS:
        return version
    return EVENT_VERSION


def event_version_deprecation_message(version: int) -> str | None:
    return EVENT_VERSION_DEPRECATIONS.get(version)


def infer_severity(
    payload: dict[str, Any], metadata: dict[str, Any] | None = None
) -> EventSeverity:
    metadata = metadata or {}
    explicit = payload.get("severity") or metadata.get("severity")
    if explicit:
        return normalize_severity(explicit)
    status = str(payload.get("status") or metadata.get("status") or "").lower()
    event_name = str(payload.get("name") or payload.get("event_type") or "").lower()
    if status in {"failed", "failure", "error", "blocked"} or "error" in event_name:
        return "error"
    if status in {"warning", "needs_review", "partial_success"} or payload.get(
        "warning"
    ):
        return "warning"
    return "info"


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
        self.pruned_count = 0
        self.session_id = session_id
        self._recent: deque[CompanyEvent] = deque(maxlen=self.history_limit)
        self._handlers: list[EventHandler] = []
        self._lock = threading.RLock()

    def publish(self, event: CompanyEvent) -> CompanyEvent:
        """Publish an event to current subscribers and retain it for replay."""

        if not isinstance(event, CompanyEvent):
            raise TypeError("EventBus.publish expects a CompanyEvent")
        with self._lock:
            self._append_recent_locked(event)
            handlers = list(self._handlers)
        for handler in handlers:
            self._deliver_to_handler(handler, event)
        return event

    async def publish_async(self, event: CompanyEvent) -> CompanyEvent:
        """Publish an event and await coroutine subscribers."""

        if not isinstance(event, CompanyEvent):
            raise TypeError("EventBus.publish_async expects a CompanyEvent")
        with self._lock:
            self._append_recent_locked(event)
            handlers = list(self._handlers)
        for handler in handlers:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        return event

    def _append_recent_locked(self, event: CompanyEvent) -> None:
        if len(self._recent) >= self.history_limit:
            self.pruned_count += 1
        self._recent.append(event)

    def set_history_limit(self, history_limit: int) -> None:
        """Resize the replay buffer and prune old events immediately."""

        new_limit = max(1, int(history_limit))
        with self._lock:
            existing = list(self._recent)
            pruned = max(0, len(existing) - new_limit)
            self.history_limit = new_limit
            self.pruned_count += pruned
            self._recent = deque(existing[-new_limit:], maxlen=new_limit)

    def subscribe(
        self, handler: EventHandler, *, replay: bool = False
    ) -> Callable[[], None]:
        """Subscribe a handler and return an unsubscribe callback."""

        with self._lock:
            self._handlers.append(handler)
            replay_events = list(self._recent) if replay else []
        for event in replay_events:
            self._deliver_to_handler(handler, event)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers:
                    self._handlers.remove(handler)

        return unsubscribe

    def _deliver_to_handler(self, handler: EventHandler, event: CompanyEvent) -> None:
        result = handler(event)
        if asyncio.iscoroutine(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                loop.create_task(result)

    def get_recent(self, limit: int = 50) -> list[CompanyEvent]:
        """Return recent events in chronological order."""

        return self.get_recent_events(limit=limit)

    def get_recent_events(
        self, filter_by_type: str | Iterable[str] | None = None, limit: int = 50
    ) -> list[CompanyEvent]:
        """Return recent events, optionally filtered by one or more event types."""

        max_events = max(0, int(limit))
        with self._lock:
            events = list(self._recent)
        if filter_by_type is not None:
            if isinstance(filter_by_type, str):
                selected = {filter_by_type}
            else:
                selected = {str(event_type) for event_type in filter_by_type}
            events = [event for event in events if event.event_type in selected]
        return events[-max_events:] if max_events else []

    def replay_to_subscriber(
        self,
        subscriber: EventHandler,
        since_timestamp: str | datetime | None = None,
        *,
        filter_by_type: str | Iterable[str] | None = None,
        limit: int | None = None,
    ) -> int:
        """Replay retained events to a late-joining subscriber and return count sent."""

        with self._lock:
            events = list(self._recent)
        if since_timestamp is not None:
            since = (
                since_timestamp.isoformat()
                if isinstance(since_timestamp, datetime)
                else str(since_timestamp)
            )
            events = [event for event in events if event.timestamp >= since]
        if filter_by_type is not None:
            if isinstance(filter_by_type, str):
                selected = {filter_by_type}
            else:
                selected = {str(event_type) for event_type in filter_by_type}
            events = [event for event in events if event.event_type in selected]
        if limit is not None:
            events = events[-max(0, int(limit)) :]
        for event in events:
            self._deliver_to_handler(subscriber, event)
        return len(events)

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
        return cls(
            event_type=event_type,
            session_id=session_id,
            payload=payload,
            severity=infer_severity(payload, metadata),
        )

    department = getattr(message, "department", None)
    if department is not None:
        event_type = (
            "deployment_completed" if department == "devops" else "department_event"
        )
        event_payload = {
            "task_id": task_id,
            "department": department,
            "status": getattr(message, "status", None),
            "artifact_type": getattr(message, "artifact_type", None),
            "payload": getattr(message, "payload", None),
            "metadata": metadata,
        }
        return EVENT_CLASSES.get(event_type, DepartmentEvent)(
            event_type=event_type,
            session_id=session_id,
            payload=event_payload,
            severity=infer_severity(event_payload, metadata),
        )

    event_type = str(getattr(message, "event_type", "company_event"))
    return EVENT_CLASSES.get(event_type, CompanyEvent)(
        event_type=event_type,
        session_id=session_id,
        payload={"message": str(message)},
        severity=normalize_severity(getattr(message, "severity", "info")),
    )
