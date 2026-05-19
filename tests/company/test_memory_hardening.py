import json

import pytest

from aider.memory import MemoryRecord, MemoryStore, ProjectMemory
from aider.memory.repository import ProjectMemoryMigrator


def test_enforce_limits_and_metrics(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    for i in range(8):
        store.append_record(
            MemoryRecord(
                scope="department:qa", kind="pattern", content=f"r{i}", tags=("qa",)
            )
        )
    removed = store.enforce_limits(max_records_per_scope=5, max_total=6)
    assert removed >= 2
    metrics = store.get_metrics()
    assert metrics["memory_records_total"] <= 6
    assert metrics["records_by_scope"]["department:qa"] <= 5
    assert metrics["skill_evidence_coverage_pct"] == 0.0


def test_compact_and_repair(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    rec = store.append_record(
        MemoryRecord(scope="project", kind="fact", content="hello")
    )
    memory.data["memory"]["records"].extend(
        [
            "broken-record",
            {"content": "missing required fields"},
            {
                "id": "bad-scope",
                "scope": "invalid:scope",
                "visibility": "project",
                "created_at": "now",
            },
        ]
    )
    memory.persist()

    assert store.repair(confirm=False)["invalid_records_removed"] == 0
    result = store.repair(confirm=True)

    assert result["invalid_records_removed"] == 2
    assert result["records_fixed"] == 1
    assert result["corrupt_records_backed_up"] == 2
    assert store.get_record(rec.id) is not None
    fixed = [
        record
        for record in store.query_records()
        if record.content == "missing required fields"
    ]
    assert fixed and fixed[0].scope == "project"
    backup = tmp_path / ".aider" / "memory_corrupt_backup.json"
    assert backup.exists()
    assert len(json.loads(backup.read_text(encoding="utf-8"))[-1]["records"]) == 2


def test_migration_v4_to_v5_safe_backup(tmp_path):
    migrator = ProjectMemoryMigrator(ProjectMemory.DEFAULTS)
    data = {"schema_version": 4, "memory": {"records": [], "threads": []}}
    migrated = migrator.migrate(data)
    assert migrated["schema_version"] == 5
    assert "memory_metrics" in migrated["observability"]
    assert migrated["memory"]["migration_log"][-1]["from_version"] == 4
    assert migrated["memory"]["migration_log"][-1]["to_version"] == 5
    assert migrated["memory"]["migration_log"][-1]["records_processed"] == 0


def test_append_rejects_legacy_visibility_aliases(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))

    with pytest.raises(ValueError, match="legacy memory visibility"):
        store.append_record(MemoryRecord(content="legacy", visibility="public"))


def test_migration_v5_normalizes_legacy_visibility_and_core_metadata(tmp_path):
    migrator = ProjectMemoryMigrator(ProjectMemory.DEFAULTS)
    data = {
        "schema_version": 4,
        "memory": {
            "records": [
                {
                    "id": "r1",
                    "content": "legacy",
                    "scope": "project",
                    "visibility": "team",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "metadata": {"skill_evidence": {"task_id": "t1"}, "custom": True},
                }
            ],
            "threads": [],
        },
    }

    migrated = migrator.migrate(data)

    record = migrated["memory"]["records"][0]
    assert record["visibility"] == "project"
    assert record["skill_evidence"] == {"task_id": "t1"}
    assert record["metadata"] == {"custom": True}
    assert migrated["migration"]["v5_visibility_records_normalized"] >= 1
    assert len(migrated["memory"]["migration_log"]) == 1
    remigrated = migrator.migrate(migrated)
    assert len(remigrated["memory"]["migration_log"]) == 1


def test_migration_v5_quarantines_invalid_records_after_validation(tmp_path, caplog):
    migrator = ProjectMemoryMigrator(ProjectMemory.DEFAULTS)
    data = {
        "schema_version": 4,
        "memory": {
            "records": [
                {
                    "id": "good",
                    "content": "valid",
                    "metadata": {
                        "scope": "project",
                        "visibility": "team",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                },
                {
                    "id": "bad",
                    "content": "invalid scope",
                    "scope": "invalid:scope",
                    "visibility": "project",
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
            ],
            "threads": [],
        },
    }

    migrated = migrator.migrate(data)

    assert [record["id"] for record in migrated["memory"]["records"]] == ["good"]
    assert migrated["memory"]["records"][0]["visibility"] == "project"
    assert migrated["migration"]["v5_corrupt_records_quarantined"] == 1
    assert migrated["memory"]["corrupt_backup"][-1]["records"][0]["id"] == "bad"
    assert "Quarantining corrupt v5 memory record" in caplog.text
