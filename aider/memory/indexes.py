from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, TYPE_CHECKING

from .records import MemoryRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import MemoryStore

INDEX_DIMENSIONS = ("scope", "kind", "department", "skill")


def build_canonical_indexes(
    records: Iterable[dict[str, Any] | MemoryRecord],
) -> dict[str, Any]:
    """Build deterministic lookup indexes for canonical memory records.

    The persisted index is intentionally small and JSON-native: each dimension maps
    a normalized key to sorted record ids.  It is a rebuildable optimization, not a
    second source of truth; records remain authoritative.
    """

    buckets: dict[str, dict[str, set[str]]] = {
        dimension: defaultdict(set) for dimension in INDEX_DIMENSIONS
    }
    record_count = 0
    for item in records:
        record = _coerce_record(item)
        if record is None:
            continue
        record_count += 1
        record_id = record.id
        for scope_key in _scope_keys(record.scope):
            buckets["scope"][scope_key].add(record_id)
        buckets["kind"][record.kind].add(record_id)
        if record.department:
            buckets["department"][str(record.department)].add(record_id)
        skill_key = _skill_key(record)
        if skill_key:
            buckets["skill"][skill_key].add(record_id)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": record_count,
        **{
            dimension: {
                key: sorted(ids)
                for key, ids in sorted(index.items(), key=lambda pair: pair[0])
            }
            for dimension, index in buckets.items()
        },
    }


def rebuild_after_compaction_batch(store: MemoryStore, *, compacted_count: int) -> int:
    """Rebuild search and canonical lookup indexes after a compaction batch."""

    count = max(0, int(compacted_count))
    if count <= 0:
        return 0
    store.rebuild_index()
    store.rebuild_canonical_indexes()
    return count


def _coerce_record(item: dict[str, Any] | MemoryRecord) -> MemoryRecord | None:
    if isinstance(item, MemoryRecord):
        return item
    if not isinstance(item, dict):
        return None
    try:
        return MemoryRecord.from_dict(item)
    except Exception:
        return None


def _scope_keys(scope: str) -> list[str]:
    parts = str(scope).split(":")
    if len(parts) <= 1:
        return [str(scope)]
    keys = [parts[0]]
    for idx in range(2, len(parts) + 1):
        keys.append(":".join(parts[:idx]))
    return keys


def _skill_key(record: MemoryRecord) -> str | None:
    if str(record.scope).startswith("skill:"):
        return str(record.scope).split(":", 1)[1]
    for source in (record.skill_evidence, record.metadata):
        if not isinstance(source, dict):
            continue
        value = (
            source.get("skill") or source.get("skill_id") or source.get("skill_name")
        )
        if value:
            return str(value)
    return None
