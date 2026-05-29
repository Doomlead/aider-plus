from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .redaction import ensure_summary_redaction
from .records import MemoryRecord, utc_now_iso
from .store import MemoryStore


def record_outcome(
    store: MemoryStore,
    record_id: str,
    outcome: str,
    context: dict[str, Any] | None = None,
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
    records = [
        MemoryRecord.from_dict(item) for item in store._record_dicts()
    ]  # noqa: SLF001

    clusters: dict[tuple[str, str, str], list[MemoryRecord]] = defaultdict(list)
    for record in records:
        ts = record.updated_at or record.created_at
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt > cutoff:
            continue
        content_key = _semantic_cluster_key(record.content)
        if not content_key:
            continue
        bucket_key = (record.scope, record.kind, content_key)
        matched_key = _matching_cluster_key(clusters.keys(), bucket_key)
        clusters[matched_key or bucket_key].append(record)

    changed = 0
    for (_, kind, _), grouped in clusters.items():
        if len(grouped) < max(2, int(max_cluster_size_before_compaction)):
            continue
        ordered = sorted(grouped, key=lambda r: r.created_at)
        primary = ordered[-1]
        originals = [rec.to_dict() for rec in ordered]
        original_ids = [rec.id for rec in ordered]

        summary_text = _summary_text(kind, ordered)
        summary_metadata = {
            "compacted_from": original_ids,
            "related_records": original_ids,
            "derived_from": original_ids,
            "co_occurs_with": original_ids,
            "supersedes": original_ids,
            "compacted_at": utc_now_iso(),
            "compaction_batch_size": len(ordered),
            "semantic_compaction": True,
            "semantic_terms": _top_terms(ordered),
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
            metadata = (
                item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            )
            metadata["archived"] = True
            metadata["retired"] = True
            metadata["archived_at"] = utc_now_iso()
            metadata["archived_by_compaction"] = summary_record.id
            metadata["related_records"] = sorted(
                {*(metadata.get("related_records") or []), summary_record.id}
            )
            metadata["derived_from"] = sorted(
                {*(metadata.get("derived_from") or []), summary_record.id}
            )
            metadata["co_occurs_with"] = sorted(
                {*(metadata.get("co_occurs_with") or []), *original_ids}
            )
            item["metadata"] = metadata
        changed += len(ordered)

    if changed:
        store.project_memory.update(
            {"memory": store._memory_namespace()}
        )  # noqa: SLF001
        store.project_memory.persist()
    return changed


def _semantic_cluster_key(content: Any) -> str:
    terms = _tokens(content)
    if not terms:
        return ""
    important = [term for term in terms if len(term) > 2]
    return " ".join(sorted(dict.fromkeys(important))[:8])


def _matching_cluster_key(
    existing: Iterable[tuple[str, str, str]], candidate: tuple[str, str, str]
) -> tuple[str, str, str] | None:
    scope, kind, key = candidate
    candidate_terms = set(key.split())
    if not candidate_terms:
        return None
    for existing_scope, existing_kind, existing_key in existing:
        if existing_scope != scope or existing_kind != kind:
            continue
        existing_terms = set(existing_key.split())
        overlap = len(candidate_terms & existing_terms) / max(
            1, len(candidate_terms | existing_terms)
        )
        if overlap >= 0.45:
            return (existing_scope, existing_kind, existing_key)
    return None


def _summary_text(kind: str, records: list[MemoryRecord]) -> str:
    terms = _top_terms(records)
    latest = str(records[-1].content or "").replace("\n", " ").strip()[:220]
    term_text = f" Key terms: {', '.join(terms[:6])}." if terms else ""
    return f"Compacted {len(records)} semantically related '{kind}' records.{term_text} Latest: {latest}"


def _top_terms(records: list[MemoryRecord]) -> list[str]:
    counts: dict[str, int] = {}
    for record in records:
        for term in set(_tokens(record.content)):
            if len(term) <= 2:
                continue
            counts[term] = counts.get(term, 0) + 1
    return [
        term
        for term, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]
    ]


def _tokens(content: Any) -> list[str]:
    token = ""
    out: list[str] = []
    for char in str(content or "").lower():
        if char.isalnum() or char in {"_", "-"}:
            token += char
        elif token:
            out.append(token)
            token = ""
    if token:
        out.append(token)
    return out
