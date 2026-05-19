from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from aider.memory import MemoryRecord, MemoryStore


@dataclass(frozen=True)
class CompanyEventRecord:
    """Standard audit record persisted under ProjectMemory.data['audit_log']."""

    event_id: str
    timestamp: str
    project_id: str
    department: str
    event_type: str
    payload_summary: str
    metadata: Dict[str, Any]

    @classmethod
    def create(cls, project_id, department, event_type, payload, metadata=None):
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return cls(
            event_id=f"{project_id}:{timestamp}:{department}:{event_type}",
            timestamp=timestamp,
            project_id=str(project_id),
            department=str(department or "orchestrator"),
            event_type=str(event_type),
            payload_summary=str(payload)[:500],
            metadata=_json_safe(metadata or {}),
        )


def append_audit_event(
    project_memory,
    project_id: str,
    department: str,
    event_type: str,
    payload: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> CompanyEventRecord:
    """Append a company event record to project_memory['audit_log'].

    This function is the sole write path for audit data; callers should not mutate
    the audit log directly.
    """
    record = CompanyEventRecord.create(
        project_id=project_id,
        department=department,
        event_type=event_type,
        payload=payload,
        metadata=metadata,
    )
    audit_log = project_memory.data.get("audit_log", [])
    if not isinstance(audit_log, list):
        audit_log = []
    audit_log.append(asdict(record))
    project_memory.update({"audit_log": audit_log})
    MemoryStore(project_memory).append_record(
        MemoryRecord(
            kind="audit_summary",
            scope=f"project:{project_id}",
            visibility="project",
            project_id=str(project_id),
            department=str(department or "orchestrator"),
            channel_id="audit_log",
            thread_id=(metadata or {}).get("task_id") if isinstance(metadata, dict) else None,
            content={
                "event_id": record.event_id,
                "event_type": record.event_type,
                "payload_summary": record.payload_summary,
                "metadata": record.metadata,
                "timestamp": record.timestamp,
            },
        )
    )
    project_memory.persist()
    return record


class AuditLogViewer:
    """Small formatter for Discord/Desktop debugging views of the audit log."""

    def __init__(self, records: Iterable[dict]):
        self.records = [record for record in records if isinstance(record, dict)]

    @classmethod
    def from_project_memory(cls, project_memory) -> "AuditLogViewer":
        records = project_memory.data.get("audit_log", [])
        return cls(records if isinstance(records, list) else [])

    def recent(self, limit: int = 10, event_type: Optional[str] = None) -> List[dict]:
        records = self.records
        if event_type:
            records = [
                record for record in records if record.get("event_type") == event_type
            ]
        return records[-limit:]

    def render_text(self, limit: int = 10, event_type: Optional[str] = None) -> str:
        rows = self.recent(limit=limit, event_type=event_type)
        if not rows:
            return "No audit events recorded."
        lines = []
        for record in rows:
            metadata = record.get("metadata", {})
            task_id = metadata.get("task_id") if isinstance(metadata, dict) else None
            suffix = f" task={task_id}" if task_id else ""
            lines.append(
                " | ".join(
                    [
                        str(record.get("timestamp", "")),
                        str(record.get("department", "orchestrator")),
                        str(record.get("event_type", "event")),
                        f"{str(record.get('payload_summary', ''))[:120]}{suffix}",
                    ]
                )
            )
        return "\n".join(lines)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
