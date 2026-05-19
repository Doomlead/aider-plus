from __future__ import annotations

from aider.memory import MemoryQuery, MemoryRecord, MemoryStore, ProjectMemory


def test_recall_precision_under_ambiguity_prefers_specific_match(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    broad = store.append_record(
        MemoryRecord(content="Set up auth", scope="project", tags=["auth"])
    )
    specific = store.append_record(
        MemoryRecord(
            content="Set up auth token rotation for API workers",
            scope="project",
            tags=["auth", "token", "rotation"],
        )
    )

    matches = store.query_records(MemoryQuery(text="token rotation", limit=2))
    assert [m.id for m in matches] == [specific.id, broad.id]


def test_legacy_fallback_parity_for_query_kwargs_vs_query_object(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    store.append_record(MemoryRecord(content="qa runbook", tags=["qa"], kind="note"))

    via_object = store.query_records(MemoryQuery(text="qa", tags=("qa",), kind="note"))
    via_kwargs = store.query_records(text="qa", tags=("qa",), kind="note")

    assert [r.id for r in via_object] == [r.id for r in via_kwargs]
