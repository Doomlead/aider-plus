from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Optional

from .records import MemoryRecord
from .scopes import SCOPE_PROJECT
from .store import MemoryStore

COMMUNICATION_EVENTS = frozenset(
    {
        "task_received",
        "deliverable_produced",
        "handoff",
        "route_decision",
        "user_instruction",
        "approval_decision",
        "failure",
    }
)


def task_received(
    store_or_memory: Any, task: Any, *, department: str | None = None
) -> MemoryRecord | None:
    return append_communication_record(
        store_or_memory,
        "task_received",
        _preview(getattr(task, "payload", "")),
        scope=_department_scope(department or getattr(task, "target", None)),
        visibility="project",
        task_id=getattr(task, "task_id", None),
        origin=getattr(task, "origin", None),
        targets=[getattr(task, "target", None)],
        thread_id=_thread_id_from_context(getattr(task, "context", None)),
        metadata={
            "department": department or getattr(task, "target", None),
            "artifact_type": getattr(task, "artifact_type", None),
            "blocking": getattr(task, "blocking", None),
        },
    )


def deliverable_produced(store_or_memory: Any, deliverable: Any) -> MemoryRecord | None:
    return append_communication_record(
        store_or_memory,
        "deliverable_produced",
        _preview(getattr(deliverable, "payload", "")),
        scope=_department_scope(getattr(deliverable, "department", None)),
        visibility="project",
        task_id=getattr(deliverable, "task_id", None),
        origin=getattr(deliverable, "department", None),
        targets=_target_list(
            getattr(deliverable, "metadata", {}).get("handoff_to")
            if isinstance(getattr(deliverable, "metadata", None), dict)
            else None
        ),
        thread_id=_thread_id_from_context(
            (getattr(deliverable, "metadata", {}) or {}).get("context")
            if isinstance(getattr(deliverable, "metadata", None), dict)
            else None
        ),
        metadata={
            "department": getattr(deliverable, "department", None),
            "artifact_type": getattr(deliverable, "artifact_type", None),
            "status": getattr(deliverable, "status", None),
        },
        skill_evidence=_skill_evidence_stub(
            getattr(deliverable, "task_id", None),
            getattr(deliverable, "department", None),
            getattr(deliverable, "status", None),
        ),
    )


def handoff(
    store_or_memory: Any,
    task: Any,
    *,
    source: str | None = None,
    reason: str | None = None,
) -> MemoryRecord | None:
    return append_communication_record(
        store_or_memory,
        "handoff",
        _preview(getattr(task, "payload", "")),
        scope=_department_scope(getattr(task, "target", None)),
        visibility="project",
        task_id=getattr(task, "task_id", None),
        origin=source or getattr(task, "origin", None),
        targets=[getattr(task, "target", None)],
        thread_id=_thread_id_from_context(getattr(task, "context", None)),
        metadata={
            "artifact_type": getattr(task, "artifact_type", None),
            "reason": reason,
            "blocking": getattr(task, "blocking", None),
        },
    )


def route_decision(
    store_or_memory: Any,
    *,
    deliverable: Any = None,
    task: Any = None,
    target: str | None = None,
    strategy: str | None = None,
    reason: str | None = None,
) -> MemoryRecord | None:
    task_id = getattr(task, "task_id", None) or getattr(deliverable, "task_id", None)
    origin = (
        getattr(task, "origin", None)
        or getattr(deliverable, "department", None)
        or "orchestrator"
    )
    context = getattr(task, "context", None)
    if context is None and isinstance(getattr(deliverable, "metadata", None), dict):
        context = deliverable.metadata.get("context")
    return append_communication_record(
        store_or_memory,
        "route_decision",
        reason
        or f"Route {origin} to {target or getattr(task, 'target', None) or 'none'}",
        scope="project",
        visibility="project",
        task_id=task_id,
        origin=origin,
        targets=_target_list(target or getattr(task, "target", None)),
        thread_id=_thread_id_from_context(context),
        metadata={"strategy": strategy, "reason": reason},
    )


def user_instruction(
    store_or_memory: Any,
    message: Any,
    *,
    surface: str = "cli",
    session_id: str | None = None,
    task_id: str | None = None,
    origin: str | None = None,
    target: str | None = None,
    visibility: str = "project",
    metadata: Optional[dict[str, Any]] = None,
) -> MemoryRecord | None:
    return append_communication_record(
        store_or_memory,
        "user_instruction",
        _preview(message, limit=2000),
        scope=f"thread:{session_id}" if session_id else SCOPE_PROJECT,
        visibility=visibility,
        task_id=task_id,
        origin=origin or surface,
        targets=_target_list(target),
        thread_id=session_id,
        metadata={"surface": surface, **(metadata or {})},
    )


def approval_decision(
    store_or_memory: Any,
    *,
    task_id: str,
    approved: bool,
    source: str | None = None,
    reason: str | None = None,
    task: Any = None,
    metadata: Optional[dict[str, Any]] = None,
) -> MemoryRecord | None:
    task_context = getattr(task, "context", None)
    return append_communication_record(
        store_or_memory,
        "approval_decision",
        "approved" if approved else "rejected",
        scope=(
            _department_scope(getattr(task, "target", None))
            if task is not None
            else SCOPE_PROJECT
        ),
        visibility="project",
        task_id=task_id,
        origin=source or (metadata or {}).get("approved_by") or "approval",
        targets=_target_list(getattr(task, "target", None)),
        thread_id=_thread_id_from_context(task_context),
        metadata={"approved": approved, "reason": reason, **(metadata or {})},
    )


def failure(
    store_or_memory: Any,
    error: Any,
    *,
    task: Any = None,
    department: str | None = None,
    stage: str | None = None,
) -> MemoryRecord | None:
    return append_communication_record(
        store_or_memory,
        "failure",
        _preview(error, limit=2000),
        scope=_department_scope(department or getattr(task, "target", None)),
        visibility="project",
        task_id=getattr(task, "task_id", None),
        origin=department or getattr(task, "target", None),
        targets=[],
        thread_id=_thread_id_from_context(getattr(task, "context", None)),
        metadata={"stage": stage, "error_type": type(error).__name__},
    )


def append_communication_record(
    store_or_memory: Any,
    event_type: str,
    content: Any,
    *,
    scope: str = SCOPE_PROJECT,
    visibility: str = "project",
    task_id: str | None = None,
    thread_id: str | None = None,
    origin: str | None = None,
    targets: Iterable[str | None] | None = None,
    metadata: Optional[dict[str, Any]] = None,
    skill_evidence: Optional[dict[str, Any]] = None,
) -> MemoryRecord | None:
    """Append a standardized communication ``MemoryRecord`` without affecting callers."""
    store = _coerce_store(store_or_memory)
    if store is None:
        return None
    safe_metadata = _json_safe(metadata or {})
    safe_metadata.update(
        {
            "event_type": event_type,
            "task_id": task_id,
            "thread_id": thread_id,
            "origin": origin,
            "targets": [target for target in (targets or []) if target],
        }
    )
    record = MemoryRecord(
        kind=event_type,
        content=content,
        scope=scope,
        visibility=visibility,
        author=origin,
        tags=["communication", event_type],
        metadata=safe_metadata,
        skill_evidence=skill_evidence,
    )
    try:
        return store.append_record(record)
    except Exception:
        return None


def _coerce_store(store_or_memory: Any) -> MemoryStore | None:
    if store_or_memory is None:
        return None
    if isinstance(store_or_memory, MemoryStore):
        return store_or_memory
    if hasattr(store_or_memory, "project_memory") and hasattr(
        store_or_memory, "append_record"
    ):
        return store_or_memory
    try:
        return MemoryStore(store_or_memory)
    except Exception:
        return None


def _department_scope(department: str | None) -> str:
    return f"department:{department}" if department else SCOPE_PROJECT


def _target_list(target: str | None) -> list[str]:
    return [target] if target else []


def _thread_id_from_context(context: Any) -> str | None:
    if not isinstance(context, dict):
        return None
    return (
        context.get("thread_id")
        or context.get("session_id")
        or context.get("session_key")
    )


def _skill_evidence_stub(
    task_id: str | None, department: str | None, status: str | None
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "role": department,
        "outcome": status,
        "signals": {},
    }


def _preview(value: Any, *, limit: int = 1000) -> str:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, (dict, list, tuple)):
        text = repr(_json_safe(value))
    else:
        text = str(value)
    return text[:limit]


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
