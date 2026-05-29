from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from .scopes import SCOPE_PROJECT, validate_scope
from .visibility import validate_visibility

MEMORY_RECORD_SCHEMA_VERSION = 1


def normalize_graph_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(metadata or {})
    edge_keys = ("co_occurs_with", "handoff_from", "handoff_to", "derived_from")
    for key in edge_keys:
        value = normalized.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            normalized[key] = [value]
            continue
        if isinstance(value, list):
            normalized[key] = [str(v) for v in value if v]
            continue
        normalized.pop(key, None)
    return normalized


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryRecord:
    """Serializable local-first memory unit.

    ``skill_evidence`` is reserved for Phase 1 evidence capture. The store keeps
    it intact but does not interpret it yet.
    """

    content: Any
    schema_version: int = MEMORY_RECORD_SCHEMA_VERSION
    scope: str = SCOPE_PROJECT
    visibility: str = "project"
    kind: str = "note"
    record_id: str = field(default_factory=lambda: f"mem_{uuid4().hex}")
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: Optional[str] = None
    author: Optional[str] = None
    department: Optional[str] = None
    project_id: Optional[str] = None
    thread_id: Optional[str] = None
    channel_id: Optional[str] = None
    user_id: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    skill_evidence: Optional[Dict[str, Any]] = None
    usage_count: int = 0
    successful_uses: int = 0
    failed_uses: int = 0
    last_used_at: Optional[str] = None
    acceptance_rate: Optional[float] = None
    reinforcement_score: Optional[float] = None

    def __post_init__(self) -> None:
        self.scope = validate_scope(self.scope)
        if not isinstance(self.tags, list):
            self.tags = list(self.tags or [])
        if self.metadata is None:
            self.metadata = {}
        if self.skill_evidence is None and isinstance(
            self.metadata.get("skill_evidence"), dict
        ):
            self.skill_evidence = dict(self.metadata.pop("skill_evidence"))
        self.usage_count = int(self.usage_count or self.metadata.pop("usage_count", 0))
        self.successful_uses = int(
            self.successful_uses or self.metadata.pop("successful_uses", 0)
        )
        self.failed_uses = int(self.failed_uses or self.metadata.pop("failed_uses", 0))
        self.last_used_at = self.last_used_at or self.metadata.pop("last_used_at", None)
        if self.acceptance_rate is None:
            rate = self.metadata.pop("acceptance_rate", None)
            self.acceptance_rate = float(rate) if rate is not None else None
        if self.reinforcement_score is None:
            score = self.metadata.pop("reinforcement_score", None)
            self.reinforcement_score = float(score) if score is not None else None

    def validate(self, *, allow_legacy_visibility: bool = True) -> None:
        """Validate required canonical fields before a record is persisted."""

        if not self.record_id:
            raise ValueError("memory record id is required")
        validate_scope(self.scope)
        self.visibility = validate_visibility(
            self.visibility, allow_legacy=allow_legacy_visibility
        )
        if not self.kind:
            raise ValueError("memory record kind is required")
        if not self.created_at:
            raise ValueError("memory record created_at is required")
        if self.skill_evidence is not None and not isinstance(
            self.skill_evidence, dict
        ):
            raise ValueError("memory record skill_evidence must be a dictionary")
        if self.usage_count < 0 or self.successful_uses < 0 or self.failed_uses < 0:
            raise ValueError("memory record usage counters must be non-negative")

    @property
    def id(self) -> str:
        return self.record_id

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": int(self.schema_version or MEMORY_RECORD_SCHEMA_VERSION),
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
        for key in ("department", "project_id", "thread_id", "channel_id", "user_id"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.skill_evidence is not None:
            payload["skill_evidence"] = dict(self.skill_evidence)
        payload["usage_count"] = int(self.usage_count)
        payload["successful_uses"] = int(self.successful_uses)
        payload["failed_uses"] = int(self.failed_uses)
        if self.last_used_at is not None:
            payload["last_used_at"] = self.last_used_at
        if self.acceptance_rate is not None:
            payload["acceptance_rate"] = float(self.acceptance_rate)
        if self.reinforcement_score is not None:
            payload["reinforcement_score"] = float(self.reinforcement_score)
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryRecord":
        if not isinstance(data, dict):
            raise ValueError("memory record must be a dictionary")
        record_id = data.get("record_id") or data.get("id")
        kind = data.get("kind") or data.get("type") or "note"
        metadata = (
            data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        )
        custom_metadata = normalize_graph_metadata(dict(metadata))
        skill_evidence = data.get("skill_evidence") or custom_metadata.pop(
            "skill_evidence", None
        )
        return cls(
            schema_version=int(
                data.get("schema_version") or MEMORY_RECORD_SCHEMA_VERSION
            ),
            record_id=str(record_id) if record_id else f"mem_{uuid4().hex}",
            kind=str(kind),
            content=data.get("content"),
            scope=str(data.get("scope") or custom_metadata.pop("scope", SCOPE_PROJECT)),
            visibility=str(
                data.get("visibility") or custom_metadata.pop("visibility", "project")
            ),
            created_at=str(
                data.get("created_at")
                or custom_metadata.pop("created_at", utc_now_iso())
            ),
            updated_at=data.get("updated_at")
            or custom_metadata.pop("updated_at", None),
            author=data.get("author") or custom_metadata.pop("author", None),
            department=data.get("department")
            or custom_metadata.pop("department", None),
            project_id=data.get("project_id")
            or custom_metadata.pop("project_id", None),
            thread_id=data.get("thread_id") or custom_metadata.pop("thread_id", None),
            channel_id=data.get("channel_id")
            or custom_metadata.pop("channel_id", None)
            or custom_metadata.pop("channel", None),
            user_id=data.get("user_id") or custom_metadata.pop("user_id", None),
            tags=list(data.get("tags") or custom_metadata.pop("tags", []) or []),
            metadata=custom_metadata,
            skill_evidence=skill_evidence,
            usage_count=int(
                data["usage_count"]
                if "usage_count" in data
                else custom_metadata.pop("usage_count", 0)
            ),
            successful_uses=int(
                data["successful_uses"]
                if "successful_uses" in data
                else custom_metadata.pop("successful_uses", 0)
            ),
            failed_uses=int(
                data["failed_uses"]
                if "failed_uses" in data
                else custom_metadata.pop("failed_uses", 0)
            ),
            last_used_at=data.get("last_used_at")
            or custom_metadata.pop("last_used_at", None),
            acceptance_rate=(
                data["acceptance_rate"]
                if "acceptance_rate" in data
                else custom_metadata.pop("acceptance_rate", None)
            ),
            reinforcement_score=(
                data["reinforcement_score"]
                if "reinforcement_score" in data
                else custom_metadata.pop("reinforcement_score", None)
            ),
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
    department: Optional[str] = None
    skill: Optional[str] = None
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
