from __future__ import annotations

import json

from aider.memory import MemoryQuery, MemoryRecord, MemoryStore, ProjectMemory


def test_memory_schema_migration_preserves_existing_project_data(tmp_path):
    memory_path = tmp_path / ".aider" / "project_memory.json"
    memory_path.parent.mkdir()
    memory_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "audit_log": [{"event_type": "kept"}],
                "playbook": {"coding_standards": ["preserve me"]},
                "skill_proposals": [{"proposal_id": "skill-1"}],
                "observability": {
                    "turns_per_phase": {"development": {"engineering": 1}},
                    "token_usage_per_department": {"engineering": 12},
                },
            }
        ),
        encoding="utf-8",
    )

    project_memory = ProjectMemory(str(tmp_path))
    data = project_memory.load()

    assert data["schema_version"] == 4
    assert data["audit_log"] == [{"event_type": "kept"}]
    assert data["playbook"]["coding_standards"] == ["preserve me"]
    assert data["skill_proposals"] == [{"proposal_id": "skill-1"}]
    assert data["observability"]["turns_per_phase"] == {
        "development": {"engineering": 1}
    }
    assert data["observability"]["token_usage_per_department"]["engineering"][
        "total_tokens"
    ] == 12
    assert data["memory"] == {"records": [], "threads": []}


def test_memory_store_appends_persists_and_queries_records(tmp_path):
    project_memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(project_memory)

    record = store.append_record(
        MemoryRecord(
            kind="decision",
            content="Use a local-first memory fabric for Phase 1.",
            scope="project",
            visibility="team",
            tags=["phase-1", "architecture"],
            metadata={"source": "test"},
        )
    )

    assert store.get_record(record.id).content == record.content
    matches = store.query_records(
        MemoryQuery(text="local-first", scope="project", tags=("phase-1",))
    )
    assert [item.id for item in matches] == [record.id]

    reloaded = ProjectMemory(str(tmp_path))
    reloaded.load()
    persisted = MemoryStore(reloaded).get_record(record.id)
    assert persisted is not None
    assert persisted.kind == "decision"
    assert persisted.metadata == {"source": "test"}


def test_memory_store_applies_basic_visibility_filtering(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    shared = store.append_record(
        MemoryRecord(content="shared rollout note", scope="shared", visibility="public")
    )
    engineering = store.append_record(
        MemoryRecord(
            content="engineering-only retry note",
            scope="role:engineering",
            visibility="team",
        )
    )
    product_private = store.append_record(
        MemoryRecord(
            content="private product discovery",
            scope="role:product",
            visibility="private",
            author="pm",
        )
    )

    engineering_view = store.query_records(
        MemoryQuery(requester_scope="role:engineering")
    )
    assert [record.id for record in engineering_view] == [shared.id, engineering.id]

    product_view = store.query_records(
        MemoryQuery(requester_scope="role:product", requester="pm")
    )
    assert [record.id for record in product_view] == [shared.id, product_private.id]

    public_only = store.query_records(MemoryQuery(visibility="public"))
    assert [record.id for record in public_only] == [shared.id]


def test_memory_record_serializes_skill_evidence_block(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    evidence = {
        "skill": "engineering/run-focused-tests",
        "task_id": "t1",
        "outcome": "success",
        "signals": {"tests": ["pytest tests/company/test_memory_fabric.py"]},
    }

    record = store.append_record(
        MemoryRecord(
            kind="skill_evidence",
            content="Focused tests helped validate the change.",
            scope="skill:engineering/run-focused-tests",
            visibility="skill",
            skill_evidence=evidence,
        )
    )

    serialized = store.get_record(record.id).to_dict()
    assert serialized["skill_evidence"] == evidence
    assert serialized["scope"] == "skill:engineering/run-focused-tests"

    reloaded = ProjectMemory(str(tmp_path))
    reloaded.load()
    loaded_record = MemoryStore(reloaded).get_record(record.id)
    assert loaded_record.skill_evidence == evidence
