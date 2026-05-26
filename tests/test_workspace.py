from __future__ import annotations

import json
from pathlib import Path

from aider.workspace import ProjectRef, TaskRef, TaskSessionPool, Workspace, WorkspaceStore


class DummySession:
    def __init__(self, name: str):
        self.name = name
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


def test_workspace_store_round_trip_and_schema_migration(tmp_path: Path):
    store = WorkspaceStore("ws", root=tmp_path)
    workspace = Workspace(
        workspace_id="ws",
        projects=[ProjectRef(project_id="p1", name="proj", path="/tmp/proj")],
        tasks=[TaskRef(project_id="p1", task_id="t1", title="task")],
        active_task_id="t1",
    )
    store.save(workspace)
    loaded = store.load()
    assert loaded.workspace_id == "ws"
    assert loaded.active_task_id == "t1"
    assert loaded.projects[0].name == "proj"

    legacy = {"workspace_id": "legacy"}
    store.path.write_text(json.dumps(legacy), encoding="utf-8")
    migrated = store.load()
    assert migrated.schema_version == 1
    assert migrated.workspace_id == "legacy"
    assert migrated.projects == []
    assert migrated.tasks == []


def test_atomic_write_recovery_ignores_orphan_temp_file(tmp_path: Path):
    store = WorkspaceStore("ws", root=tmp_path)
    workspace = Workspace(workspace_id="ws")
    store.save(workspace)
    # Simulate interrupted write by creating an orphan temp file in workspace dir.
    orphan = tmp_path / "tmp-incomplete.json"
    orphan.write_text('{"workspace_id": "broken"', encoding="utf-8")

    loaded = store.load()
    assert loaded.workspace_id == "ws"


def test_task_session_pool_eviction_order_and_shutdown():
    pool = TaskSessionPool(max_active=2)
    sessions = {}

    def make(name: str):
        obj = DummySession(name)
        sessions[name] = obj
        return obj

    pool.get_or_create("a", lambda: make("a"))
    pool.get_or_create("b", lambda: make("b"))
    pool.get_or_create("a", lambda: make("a2"))  # touch a, b should become LRU
    pool.get_or_create("c", lambda: make("c"))

    assert sessions["b"].shutdown_calls == 1
    assert sessions["a"].shutdown_calls == 0
    assert sessions["c"].shutdown_calls == 0
