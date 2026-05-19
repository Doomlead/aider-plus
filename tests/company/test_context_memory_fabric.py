from __future__ import annotations

from aider.memory import MemoryRecord, MemoryStore, ProjectMemory


def test_token_budget_caps_are_deterministic_and_migration_parity_safe(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    for idx in range(10):
        store.append_record(
            MemoryRecord(
                kind="context",
                scope="project",
                content=f"Context chunk {idx} " + ("detail " * 40),
                metadata={"token_cost_estimate": 50},
            )
        )

    removed = store.enforce_limits(max_records_per_scope=8, max_total=8)
    assert removed == 2

    records = store.query_records(scope="project")
    assert len(records) == 8
    assert records[0].content.startswith("Context chunk 2")
