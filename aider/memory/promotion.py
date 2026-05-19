from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .redaction import ensure_summary_redaction
from .records import MemoryRecord, utc_now_iso
from .store import MemoryStore


def record_outcome(
    store: MemoryStore, record_id: str, outcome: str, context: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    return store.record_outcome(record_id=record_id, outcome=outcome, context=context)


def compact_near_duplicates(
    store: MemoryStore,
    *,
    older_than_days: int = 60,
    max_cluster_size_before_compaction: int = 12,
) -> int:
    """Summarize near-duplicate old records and archive originals (non-destructive)."""

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, int(older_than_days)))
    records = [MemoryRecord.from_dict(item) for item in store._record_dicts()]  # noqa: SLF001

    clusters: dict[tuple[str, str, str], list[MemoryRecord]] = defaultdict(list)
    for record in records:
        ts = record.updated_at or record.created_at
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt > cutoff:
            continue
        content_key = str(record.content or "")[:120].strip().lower()
        if not content_key:
            continue
        clusters[(record.scope, record.kind, content_key)].append(record)

    changed = 0
    for (_, kind, _), grouped in clusters.items():
        if len(grouped) < max(2, int(max_cluster_size_before_compaction)):
            continue
        ordered = sorted(grouped, key=lambda r: r.created_at)
        primary = ordered[-1]
        originals = [rec.to_dict() for rec in ordered]
        original_ids = [rec.id for rec in ordered]

        summary_text = f"Compacted {len(ordered)} near-duplicate '{kind}' records. Latest: {primary.content}"
        summary_metadata = {
            "compacted_from": original_ids,
            "related_records": original_ids,
            "derived_from": original_ids,
            "co_occurs_with": original_ids,
            "supersedes": original_ids,
            "compacted_at": utc_now_iso(),
            "compaction_batch_size": len(ordered),
        }
        summary_metadata = ensure_summary_redaction(summary_metadata, originals)

        summary_record = MemoryRecord(
            content=summary_text,
            kind=f"{kind}_cluster_summary",
            scope=primary.scope,
            visibility=primary.visibility,
            tags=sorted({*primary.tags, "compacted", "cluster_summary"}),
            metadata=summary_metadata,
            project_id=primary.project_id,
            thread_id=primary.thread_id,
            channel_id=primary.channel_id,
            department=primary.department,
            user_id=primary.user_id,
            author="memory_compactor",
        )
        store.append_record(summary_record)

        for item in store._record_dicts():  # noqa: SLF001
            item_id = str(item.get("id") or item.get("record_id") or "")
            if item_id not in original_ids:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            metadata["archived"] = True
            metadata["retired"] = True
            metadata["archived_at"] = utc_now_iso()
            metadata["archived_by_compaction"] = summary_record.id
            metadata["related_records"] = sorted({*(metadata.get("related_records") or []), summary_record.id})
            metadata["derived_from"] = sorted({*(metadata.get("derived_from") or []), summary_record.id})
            metadata["co_occurs_with"] = sorted({*(metadata.get("co_occurs_with") or []), *original_ids})
            item["metadata"] = metadata
        changed += len(ordered)

    if changed:
        store.project_memory.update({"memory": store._memory_namespace()})  # noqa: SLF001
        store.project_memory.persist()
    return changed
