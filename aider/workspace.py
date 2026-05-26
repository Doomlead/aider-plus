from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProjectRef:
    project_id: str
    name: str
    path: str
    default_branch: str = "main"
    last_opened: str = field(default_factory=utc_now)


@dataclass
class TaskRef:
    project_id: str
    task_id: str
    title: str
    status: str = "active"
    branch: str = ""
    worktree_path: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    last_opened: str = field(default_factory=utc_now)
    model_settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class Workspace:
    workspace_id: str
    schema_version: int = 1
    projects: list[ProjectRef] = field(default_factory=list)
    tasks: list[TaskRef] = field(default_factory=list)
    active_task_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Workspace":
        return cls(
            workspace_id=data.get("workspace_id") or "default",
            schema_version=int(data.get("schema_version", 1)),
            projects=[ProjectRef(**p) for p in data.get("projects", [])],
            tasks=[TaskRef(**t) for t in data.get("tasks", [])],
            active_task_id=data.get("active_task_id"),
        )


class WorkspaceStore:
    def __init__(self, workspace_id: str = "default", root: Path | None = None):
        self.workspace_id = workspace_id
        self.root = root or (Path.home() / ".aider" / "workspaces")
        self.path = self.root / f"{workspace_id}.json"

    def load(self) -> Workspace:
        if not self.path.exists():
            return Workspace(workspace_id=self.workspace_id)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data = self._migrate(data)
        return Workspace.from_dict(data)

    def _migrate(self, data: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(data or {})
        version = int(migrated.get("schema_version", 0) or 0)
        if version < 1:
            migrated.setdefault("workspace_id", self.workspace_id)
            migrated.setdefault("projects", [])
            migrated.setdefault("tasks", [])
            migrated.setdefault("active_task_id", None)
            migrated["schema_version"] = 1
        return migrated

    def save(self, workspace: Workspace) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = asdict(workspace)
        payload["schema_version"] = 1
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(self.root), encoding="utf-8") as tmp:
            json.dump(payload, tmp, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, self.path)


class TaskSessionPool:
    def __init__(self, max_active: int = 4):
        self.max_active = max_active
        self._sessions: OrderedDict[str, Any] = OrderedDict()

    def get_or_create(self, key: str, factory: Callable[[], Any]) -> Any:
        session = self._sessions.pop(key, None)
        if session is None:
            session = factory()
        self._sessions[key] = session
        while len(self._sessions) > self.max_active:
            _, old = self._sessions.popitem(last=False)
            shutdown = getattr(old, "shutdown", None)
            if callable(shutdown):
                shutdown()
        return session


def ensure_default_task(workspace: Workspace, project: ProjectRef) -> TaskRef:
    for task in workspace.tasks:
        if task.project_id == project.project_id and task.status == "active":
            return task
    task = TaskRef(
        project_id=project.project_id,
        task_id=str(uuid.uuid4()),
        title=f"{project.name} default task",
        worktree_path=project.path,
    )
    workspace.tasks.append(task)
    workspace.active_task_id = task.task_id
    return task
