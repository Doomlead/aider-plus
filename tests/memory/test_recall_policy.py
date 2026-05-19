from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aider.memory import MemoryRecord, MemoryStore, ProjectMemory
from aider.memory.explanations import explanation_telemetry, format_recall_explanation


def test_reinforcement_learning_effects_raise_ranked_recall(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    baseline = store.append_record(MemoryRecord(content="retry backoff playbook", tags=["retry"]))
    candidate = store.append_record(MemoryRecord(content="retry backoff playbook", tags=["retry", "jitter"]))

    initial = store.query_records(text="retry backoff", limit=2)
    assert len(initial) == 2

    for _ in range(5):
        store.record_outcome(candidate.id, "success")
    for _ in range(5):
        store.record_outcome(baseline.id, "failure")

    boosted = store.query_records(text="retry backoff", limit=2)
    assert boosted[0].id == candidate.id


def test_explanation_completeness_has_required_fields():
    explanation = format_recall_explanation(
        label="memory_record",
        matching_terms=["token", "rotation"],
        scope_reason="scope matched project",
        confidence=0.88,
        updated_at=datetime.now(timezone.utc).isoformat(),
        evidence_count=3,
    )
    telemetry = explanation_telemetry(
        label="memory_record",
        matching_terms=["token", "rotation"],
        scope_reason="scope matched project",
        confidence=0.88,
        updated_at=datetime.now(timezone.utc).isoformat(),
        evidence_count=3,
    )

    assert "match_terms=" in explanation
    assert "scope_reason=" in explanation
    assert "confidence=" in explanation
    assert "freshness=" in explanation
    assert "evidence_count=" in explanation
    assert set(telemetry) >= {
        "label",
        "matching_terms",
        "scope_reason",
        "confidence",
        "freshness",
        "updated_at",
        "evidence_count",
    }


def test_decay_compaction_safety_does_not_prune_reinforced_records(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    old = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()

    reinforced = store.append_record(
        MemoryRecord(content="stable deploy checklist", created_at=old, metadata={"reinforcement_signal": 3})
    )
    decayed = store.append_record(
        MemoryRecord(content="obsolete deploy checklist", created_at=old, metadata={"reinforcement_signal": -3})
    )

    doomed = store.compact(threshold_days=90, min_signal=-2, dry_run=True)
    assert doomed == 1

    pruned = store.compact(threshold_days=90, min_signal=-2, dry_run=False)
    assert pruned == 1
    assert store.get_record(reinforced.id) is not None
    assert store.get_record(decayed.id) is None
