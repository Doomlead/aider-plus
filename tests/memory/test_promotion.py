from __future__ import annotations

from aider.memory import MemoryRecord, MemoryStore, ProjectMemory
from aider.memory.promotion import compact_near_duplicates


def test_compaction_creates_summary_and_archives_originals(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    text = "Incident response runbook for pager escalation"
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    for _ in range(3):
        store.append_record(MemoryRecord(kind="note", content=text, scope="project", created_at=old))

    changed = compact_near_duplicates(
        store,
        older_than_days=0,
        max_cluster_size_before_compaction=3,
    )

    assert changed == 3
    summaries = [r for r in store.query_records() if r.kind.endswith("_cluster_summary")]
    assert len(summaries) == 1

    originals = [r for r in store.query_records() if r.content == text]
    assert len(originals) == 3
    assert all(r.metadata.get("archived") is True for r in originals)
