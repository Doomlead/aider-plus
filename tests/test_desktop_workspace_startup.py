from __future__ import annotations

from pathlib import Path

from aider.desktop import AiderPlusDesktop
from aider.workspace import ProjectRef, TaskRef, Workspace, WorkspaceStore


class DummyTk:
    def title(self, *_):
        pass

    def geometry(self, *_):
        pass

    def minsize(self, *_):
        pass

    def after(self, *_):
        pass


def test_desktop_startup_loads_empty_workspace(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aider.desktop.tk.Tk", DummyTk)
    monkeypatch.setattr(AiderPlusDesktop, "_setup_style", lambda self: None)
    monkeypatch.setattr(AiderPlusDesktop, "_build_ui", lambda self: None)
    monkeypatch.setattr(AiderPlusDesktop, "_init_backend", lambda self: None)
    monkeypatch.setattr(AiderPlusDesktop, "_set_busy", lambda self, *_args, **_kwargs: None)
    monkeypatch.setattr("aider.desktop.WorkspaceStore", lambda _id: WorkspaceStore(_id, root=tmp_path))

    app = AiderPlusDesktop(argv=[])
    assert app.workspace.workspace_id == "desktop"
    assert app.workspace.projects == []
    assert app.workspace.tasks == []


def test_desktop_startup_loads_populated_workspace(monkeypatch, tmp_path: Path):
    store = WorkspaceStore("desktop", root=tmp_path)
    ws = Workspace(
        workspace_id="desktop",
        projects=[ProjectRef(project_id="p1", name="proj", path="/repo")],
        tasks=[TaskRef(project_id="p1", task_id="t1", title="task-1")],
        active_task_id="t1",
    )
    store.save(ws)

    monkeypatch.setattr("aider.desktop.tk.Tk", DummyTk)
    monkeypatch.setattr(AiderPlusDesktop, "_setup_style", lambda self: None)
    monkeypatch.setattr(AiderPlusDesktop, "_build_ui", lambda self: None)
    monkeypatch.setattr(AiderPlusDesktop, "_init_backend", lambda self: None)
    monkeypatch.setattr(AiderPlusDesktop, "_set_busy", lambda self, *_args, **_kwargs: None)
    monkeypatch.setattr("aider.desktop.WorkspaceStore", lambda _id: WorkspaceStore(_id, root=tmp_path))

    app = AiderPlusDesktop(argv=[])
    assert len(app.workspace.projects) == 1
    assert app.workspace.projects[0].project_id == "p1"
    assert app.workspace.active_task_id == "t1"
