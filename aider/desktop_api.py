from __future__ import annotations

import secrets
import socket
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aider.workspace import ProjectRef, TaskRef, WorkspaceStore


class ProjectIn(BaseModel):
    name: str
    path: str
    default_branch: str = "main"


class TaskIn(BaseModel):
    project_id: str
    title: str
    branch: str = ""
    worktree_path: str = ""


class ChatIn(BaseModel):
    prompt: str
    target: str = "workflow"


class RunIn(BaseModel):
    prompt: str


class ApprovalIn(BaseModel):
    action: str = Field(pattern="^(approve|reject|request_changes)$")
    feedback: str = ""


class TerminalExecIn(BaseModel):
    command: str
    allow_high_risk: bool = False


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _host, port = sock.getsockname()
    sock.close()
    return int(port)


class DesktopApiServer:
    def __init__(
        self,
        workspace_store: WorkspaceStore,
        session_provider: Callable[[str], Any],
        terminal_executor: Callable[[str, str, bool], dict[str, Any]] | None = None,
    ):
        self.workspace_store = workspace_store
        self.session_provider = session_provider
        self.terminal_executor = terminal_executor
        self.token = secrets.token_urlsafe(32)
        self.host = "127.0.0.1"
        self.port = _free_port()
        self._thread: threading.Thread | None = None
        self._server: Any | None = None
        self._events: list[dict[str, Any]] = []
        self.app = self._build_app()

    def start(self):
        import uvicorn

        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True

    def _event(self, payload: dict[str, Any]):
        self._events.append(payload)
        if len(self._events) > 500:
            self._events = self._events[-500:]

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Aider Desktop Local Control Plane")

        def require_token(x_desktop_token: str | None = Header(default=None)):
            if x_desktop_token != self.token:
                raise HTTPException(status_code=401, detail="invalid token")

        @app.get("/health")
        def health():
            return {"ok": True, "bind": f"{self.host}:{self.port}"}

        @app.get("/projects")
        def list_projects(x_desktop_token: str | None = Header(default=None)):
            require_token(x_desktop_token)
            workspace = self.workspace_store.load()
            return [asdict(project) for project in workspace.projects]

        @app.post("/projects")
        def create_project(payload: ProjectIn = Body(...), x_desktop_token: str | None = Header(default=None)):
            require_token(x_desktop_token)
            workspace = self.workspace_store.load()
            project = ProjectRef(
                project_id=secrets.token_hex(8),
                name=payload.name,
                path=str(Path(payload.path)),
                default_branch=payload.default_branch,
            )
            workspace.projects.append(project)
            self.workspace_store.save(workspace)
            self._event({"kind": "project_created", "project_id": project.project_id})
            return asdict(project)

        @app.get("/tasks")
        def list_tasks(x_desktop_token: str | None = Header(default=None)):
            require_token(x_desktop_token)
            workspace = self.workspace_store.load()
            return [asdict(task) for task in workspace.tasks]

        @app.post("/tasks")
        def create_task(payload: TaskIn = Body(...), x_desktop_token: str | None = Header(default=None)):
            require_token(x_desktop_token)
            workspace = self.workspace_store.load()
            task = TaskRef(
                project_id=payload.project_id,
                task_id=secrets.token_hex(8),
                title=payload.title,
                branch=payload.branch,
                worktree_path=payload.worktree_path,
            )
            workspace.tasks.append(task)
            workspace.active_task_id = task.task_id
            self.workspace_store.save(workspace)
            self._event({"kind": "task_created", "task_id": task.task_id})
            return asdict(task)

        @app.post("/tasks/{task_id}/chat")
        def task_chat(task_id: str, payload: ChatIn = Body(...), x_desktop_token: str | None = Header(default=None)):
            require_token(x_desktop_token)
            session = self.session_provider(task_id)
            if payload.target.lower() in {"workflow", "company"}:
                future = session.run_auto(payload.prompt)
            else:
                future = session.chat_with_agent(payload.target, payload.prompt)
            self._event({"kind": "task_chat", "task_id": task_id, "target": payload.target})
            return {"accepted": True, "task_id": task_id, "future": str(future)}

        @app.post("/tasks/{task_id}/run")
        def task_run(task_id: str, payload: RunIn = Body(...), x_desktop_token: str | None = Header(default=None)):
            require_token(x_desktop_token)
            session = self.session_provider(task_id)
            future = session.run_instruction(payload.prompt)
            self._event({"kind": "task_run", "task_id": task_id})
            return {"accepted": True, "task_id": task_id, "future": str(future)}

        @app.get("/tasks/{task_id}/approvals")
        def task_approvals(task_id: str, x_desktop_token: str | None = Header(default=None)):
            require_token(x_desktop_token)
            session = self.session_provider(task_id)
            return {"task_id": task_id, "approvals": session.pending_approvals()}

        @app.post("/tasks/{task_id}/approvals")
        def task_approvals_action(
            task_id: str, payload: ApprovalIn = Body(...), x_desktop_token: str | None = Header(default=None)
        ):
            require_token(x_desktop_token)
            session = self.session_provider(task_id)
            if payload.action == "approve":
                future = session.approve(task_id, payload.feedback)
            elif payload.action == "request_changes":
                future = session.request_changes(task_id, payload.feedback)
            else:
                future = session.reject(task_id, payload.feedback or "Rejected via desktop API")
            self._event({"kind": "approval_action", "task_id": task_id, "action": payload.action})
            return {"accepted": True, "future": str(future)}

        @app.post("/tasks/{task_id}/terminal/exec")
        def task_terminal_exec(
            task_id: str, payload: TerminalExecIn = Body(...), x_desktop_token: str | None = Header(default=None)
        ):
            require_token(x_desktop_token)
            if not payload.allow_high_risk:
                raise HTTPException(status_code=403, detail="high-risk endpoint requires allow_high_risk=true")
            if self.terminal_executor is not None:
                result = self.terminal_executor(task_id, payload.command, payload.allow_high_risk)
                if not result.get("accepted"):
                    raise HTTPException(status_code=403, detail=result.get("reason", "terminal policy denied command"))
            self._event({"kind": "terminal_exec", "task_id": task_id, "command": payload.command})
            return {"accepted": True}

        @app.get("/events/stream")
        def stream_events(x_desktop_token: str | None = Header(default=None)):
            require_token(x_desktop_token)

            def gen():
                index = 0
                while True:
                    if index < len(self._events):
                        event = self._events[index]
                        index += 1
                        yield f"event: {event.get('kind', 'message')}\n"
                        yield f"data: {event}\n\n"
                    else:
                        yield "event: heartbeat\ndata: {}\n\n"
                        import time
                        time.sleep(1)

            return StreamingResponse(gen(), media_type="text/event-stream")

        return app
