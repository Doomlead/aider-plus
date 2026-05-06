from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class CompanyEventRecord:
    timestamp: str
    project_id: str
    department: str
    event_type: str
    payload_summary: str
    metadata: Dict[str, Any]

    @classmethod
    def create(cls, project_id, department, event_type, payload, metadata=None):
        return cls(
            timestamp=datetime.utcnow().isoformat(),
            project_id=project_id,
            department=department,
            event_type=event_type,
            payload_summary=str(payload)[:500],
            metadata=metadata or {},
        )


def append_audit_event(
    project_memory,
    project_id: str,
    department: str,
    event_type: str,
    payload: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> CompanyEventRecord:
    """Append a company event record to project_memory["audit_log"]."""
    record = CompanyEventRecord.create(
        project_id=project_id,
        department=department,
        event_type=event_type,
        payload=payload,
        metadata=_json_safe(metadata or {}),
    )
    audit_log = project_memory.data.get("audit_log", [])
    if not isinstance(audit_log, list):
        audit_log = []
    audit_log.append(asdict(record))
    project_memory.update({"audit_log": audit_log})
    project_memory.persist()
    return record


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
