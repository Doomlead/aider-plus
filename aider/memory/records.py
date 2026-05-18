from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from .scopes import SCOPE_PROJECT, validate_scope


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryRecord:
    """Serializable local-first memory unit.

    ``skill_evidence`` is reserved for Phase 1 evidence capture. The store keeps
    it intact but does not interpret it yet.
    """

    content: Any
    scope: str = SCOPE_PROJECT
    visibility: str = "team"
    kind: str = "note"
    record_id: str = field(default_factory=lambda: f"mem_{uuid4().hex}")
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: Optional[str] = None
    author: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    skill_evidence: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.scope = validate_scope(self.scope)
        if not isinstance(self.tags, list):
            self.tags = list(self.tags or [])
        if self.metadata is None:
            self.metadata = {}

    @property
    def id(self) -> str:
        return self.record_id

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.record_id,
            "record_id": self.record_id,
            "kind": self.kind,
            "content": self.content,
            "scope": self.scope,
            "visibility": self.visibility,
            "created_at": self.created_at,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }
        if self.updated_at is not None:
            payload["updated_at"] = self.updated_at
        if self.author is not None:
            payload["author"] = self.author
        if self.skill_evidence is not None:
            payload["skill_evidence"] = dict(self.skill_evidence)
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryRecord":
        if not isinstance(data, dict):
            raise ValueError("memory record must be a dictionary")
        record_id = data.get("record_id") or data.get("id")
        kind = data.get("kind") or data.get("type") or "note"
        return cls(
            record_id=str(record_id) if record_id else f"mem_{uuid4().hex}",
            kind=str(kind),
            content=data.get("content"),
            scope=str(data.get("scope") or SCOPE_PROJECT),
            visibility=str(data.get("visibility") or "team"),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=data.get("updated_at"),
            author=data.get("author"),
            tags=list(data.get("tags") or []),
            metadata=dict(data.get("metadata") or {}),
            skill_evidence=data.get("skill_evidence"),
        )


@dataclass(frozen=True)
class MemoryQuery:
    """Simple query object for Phase 1 memory lookup."""

    text: Optional[str] = None
    scope: Optional[str] = None
    requester_scope: Optional[str] = None
    requester: Optional[str] = None
    visibility: Optional[str] = None
    kind: Optional[str] = None
    tags: tuple[str, ...] = ()
    limit: Optional[int] = None

    def __post_init__(self) -> None:
        if self.scope is not None:
            object.__setattr__(self, "scope", validate_scope(self.scope))
        if self.requester_scope is not None:
            object.__setattr__(
                self, "requester_scope", validate_scope(self.requester_scope)
            )
        if not isinstance(self.tags, tuple):
            object.__setattr__(self, "tags", tuple(self.tags or ()))

    @classmethod
    def from_kwargs(cls, **kwargs: Any) -> "MemoryQuery":
        return cls(**kwargs)


def ensure_record(record: MemoryRecord | Dict[str, Any]) -> MemoryRecord:
    if isinstance(record, MemoryRecord):
        return record
    return MemoryRecord.from_dict(record)


def records_to_dicts(records: Iterable[MemoryRecord]) -> list[Dict[str, Any]]:
    return [record.to_dict() for record in records]
