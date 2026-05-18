from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any


def summarize_memory_metrics(memory_namespace: dict[str, Any]) -> dict[str, Any]:
    records = [
        item
        for item in memory_namespace.get("records", [])
        if isinstance(item, dict)
    ]
    by_scope = Counter(str(item.get("scope") or "unknown") for item in records)
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

    return {
        "memory_records_total": len(records),
        "records_by_scope": dict(by_scope),
        "stale_memory_count": stale_count,
    }
