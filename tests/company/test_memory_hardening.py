from aider.memory import MemoryRecord, MemoryStore, ProjectMemory
from aider.memory.repository import ProjectMemoryMigrator


def test_enforce_limits_and_metrics(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    for i in range(8):
        store.append_record(MemoryRecord(scope='department:qa', kind='pattern', content=f'r{i}', tags=('qa',)))
    removed = store.enforce_limits(max_records_per_scope=5, max_total=6)
    assert removed >= 2
    metrics = store.get_metrics()
    assert metrics['memory_records_total'] <= 6
    assert metrics['records_by_scope']['department:qa'] <= 5


def test_compact_and_repair(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    rec = store.append_record(MemoryRecord(scope='project', kind='fact', content='hello'))
    # corrupt one record
    memory.data['memory']['records'].append('broken-record')
    memory.persist()
    assert store.repair(confirm=False)['invalid_records_removed'] == 0
    result = store.repair(confirm=True)
    assert result['invalid_records_removed'] == 0
    assert store.get_record(rec.id) is not None


def test_migration_v4_to_v5_safe_backup(tmp_path):
    migrator = ProjectMemoryMigrator(ProjectMemory.DEFAULTS)
    data = {'schema_version': 4, 'memory': {'records': [], 'threads': []}}
    migrated = migrator.migrate(data)
    assert migrated['schema_version'] == 5
    assert 'memory_metrics' in migrated['observability']
