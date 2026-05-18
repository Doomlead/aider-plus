from aider.memory import LocalTFIDFIndex, MemoryQuery, MemoryRecord, MemoryStore, ProjectMemory, SQLiteFTSIndex


def test_local_tfidf_index_ranks_text_matches_first(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)), index=LocalTFIDFIndex())
    noisy = store.append_record(MemoryRecord(content="Deploy docs updates", scope="project"))
    target = store.append_record(MemoryRecord(content="Fix payment retry webhook bug", scope="project"))

    results = store.query_records(MemoryQuery(scope="project", text="payment webhook retry"))

    assert results
    assert results[0].id == target.id
    assert any(record.id == noisy.id for record in results)


def test_sqlite_fts_index_available_and_fallback_safe(tmp_path):
    db_path = tmp_path / "memory_index.db"
    store = MemoryStore(ProjectMemory(str(tmp_path)), index=SQLiteFTSIndex(db_path=db_path))
    store.append_record(MemoryRecord(content="customer auth issue", scope="project"))
    best = store.append_record(MemoryRecord(content="checkout auth token refresh", scope="project"))

    results = store.query_records(MemoryQuery(scope="project", text="auth token"))

    assert results[0].id == best.id


def test_rebuild_index_keeps_query_behavior(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    expected = store.append_record(MemoryRecord(content="SSE streaming fix for daemon", scope="project"))
    store.append_record(MemoryRecord(content="UI polish notes", scope="project"))

    store.rebuild_index()
    results = store.query_records(MemoryQuery(scope="project", text="streaming daemon"))

    assert results[0].id == expected.id
