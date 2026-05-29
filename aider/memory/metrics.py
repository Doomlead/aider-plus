from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any


def summarize_memory_metrics(memory_namespace: dict[str, Any]) -> dict[str, Any]:
    records = [
        item for item in memory_namespace.get("records", []) if isinstance(item, dict)
    ]
    by_scope = Counter(str(item.get("scope") or "unknown") for item in records)
    by_kind = Counter(str(item.get("kind") or "unknown") for item in records)
    by_department = Counter(
        str(item.get("department") or "unknown") for item in records
    )
    by_skill = Counter(
        _skill_key(item) for item in records if _skill_key(item) is not None
    )
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    stale_count = 0
    for item in records:
        updated = item.get("updated_at") or item.get("created_at")
        if not updated:
            stale_count += 1
            continue
        try:
            parsed = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        except ValueError:
            stale_count += 1
            continue
        if parsed < stale_cutoff:
            stale_count += 1

    evidence_count = sum(
        1 for item in records if isinstance(item.get("skill_evidence"), dict)
    )
    evidence_pct = round((evidence_count / len(records)) * 100.0, 1) if records else 0.0

    return {
        "memory_records_total": len(records),
        "total_records": len(records),
        "records_by_scope": dict(by_scope),
        "records_by_kind": dict(by_kind),
        "records_by_department": dict(by_department),
        "records_by_skill": dict(by_skill),
        "stale_memory_count": stale_count,
        "stale_count": stale_count,
        "skill_evidence_records": evidence_count,
        "skill_evidence_coverage_pct": evidence_pct,
    }


def _skill_key(item: dict[str, Any]) -> str | None:
    scope = str(item.get("scope") or "")
    if scope.startswith("skill:"):
        return scope.split(":", 1)[1]
    for source in (item.get("skill_evidence"), item.get("metadata")):
        if not isinstance(source, dict):
            continue
        value = (
            source.get("skill") or source.get("skill_id") or source.get("skill_name")
        )
        if value:
            return str(value)
    return None
