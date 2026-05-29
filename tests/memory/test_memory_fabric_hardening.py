from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aider.memory import (
    LocalVectorIndex,
    MemoryRecord,
    MemoryStore,
    ProjectMemory,
    TenantMemoryPolicy,
    classify_text,
)
from aider.memory.evidence import SkillEvidenceCluster
from aider.memory.promotion import compact_near_duplicates


def test_local_vector_index_ranks_semantic_records(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)), index=LocalVectorIndex())
    target = store.append_record(
        MemoryRecord(content="database migration rollback checklist", tags=["db"])
    )
    store.append_record(MemoryRecord(content="front-end color palette decisions"))

    ranked = store.query_records(text="migration rollback", limit=1)

    assert ranked[0].id == target.id
    assert store.index.health_check()["backend"] == "local_vector"


def test_semantic_compaction_clusters_related_old_records(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    contents = [
        "Pager incident escalation runbook requires QA handoff",
        "QA handoff runbook for pager incident escalation",
        "Incident escalation pager checklist before QA handoff",
    ]
    for content in contents:
        store.append_record(MemoryRecord(kind="note", content=content, created_at=old))

    changed = compact_near_duplicates(
        store,
        older_than_days=30,
        max_cluster_size_before_compaction=3,
    )

    assert changed == 3
    summary = [r for r in store.query_records() if r.kind == "note_cluster_summary"][0]
    assert summary.metadata["semantic_compaction"] is True
    assert "incident" in summary.metadata["semantic_terms"]


def test_secret_classifier_redacts_default_and_strict_policy_blocks(tmp_path):
    result = classify_text(
        "contact admin@example.com with ghp_abcdefghijklmnopqrstuvwxyz1234"
    )
    assert result.contains_pii is True
    assert result.contains_secret is True
    assert "github_token" in result.secret_types

    store = MemoryStore(ProjectMemory(str(tmp_path)))
    record = store.append_record(
        MemoryRecord(content="Email admin@example.com for support")
    )
    assert "[EMAIL_REDACTED]" in record.content
    assert record.metadata["contains_pii"] is True

    strict = TenantMemoryPolicy(max_allowed_pii=0)
    strict_store = MemoryStore(
        ProjectMemory(str(tmp_path / "strict")), tenant_policy=strict
    )
    with pytest.raises(ValueError, match="policy"):
        strict_store.append_record(MemoryRecord(content="Email admin@example.com"))


def test_cross_project_evidence_score_requires_multiple_projects():
    records = []
    for project_id, task_id in [("alpha", "t1"), ("beta", "t2"), ("beta", "t3")]:
        records.append(
            MemoryRecord(
                kind="deliverable_produced",
                content="QA release checklist passed",
                project_id=project_id,
                department="qa",
                metadata={"task_id": task_id, "status": "success"},
                successful_uses=2,
            )
        )
    cluster = SkillEvidenceCluster(
        cluster_id="qa-release",
        department="qa",
        channel="release",
        thread_id="thread",
        outcome="success",
        records=records,
        procedure_steps=["Run release checklist", "Attach proof"],
    )

    assert cluster.evidence_score >= 0.72
    assert cluster.allows_cross_project_promotion()

    single_project = SkillEvidenceCluster(
        cluster_id="qa-alpha",
        department="qa",
        channel="release",
        thread_id="thread",
        outcome="success",
        records=records[:1],
        procedure_steps=["Run release checklist"],
    )
    assert not single_project.allows_cross_project_promotion()
