from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aider.desktop_api import DesktopApiServer
from aider.workspace import WorkspaceStore


class _Session:
    def run_auto(self, _prompt: str):
        return "ok-chat"

    def chat_with_agent(self, _target: str, _prompt: str):
        return "ok-agent-chat"

    def run_instruction(self, _prompt: str):
        return "ok-run"

    def pending_approvals(self):
        return [{"task_id": "t1", "status": "pending"}]

    def approve(self, _task_id: str, _feedback: str):
        return "ok-approve"

    def reject(self, _task_id: str, _feedback: str):
        return "ok-reject"

    def request_changes(self, _task_id: str, _feedback: str):
        return "ok-revise"


def _mk_client(tmp_path: Path, terminal_executor=None):
    store = WorkspaceStore("desktop-test", root=tmp_path)
    api = DesktopApiServer(store, lambda _task_id: _Session(), terminal_executor=terminal_executor)
    client = TestClient(api.app)
    return api, client


def test_auth_required_for_non_health_endpoints(tmp_path: Path):
    api, client = _mk_client(tmp_path)
    assert client.get("/health").status_code == 200
    assert client.get("/projects").status_code == 401
    assert client.get("/projects", headers={"X-Desktop-Token": "wrong"}).status_code == 401
    assert client.get("/projects", headers={"X-Desktop-Token": api.token}).status_code == 200


def test_projects_and_tasks_crud_and_task_routes(tmp_path: Path):
    api, client = _mk_client(tmp_path)
    headers = {"X-Desktop-Token": api.token}
    p = client.post("/projects", json={"name": "p", "path": "/tmp/p"}, headers=headers)
    assert p.status_code == 200
    pid = p.json()["project_id"]
    t = client.post("/tasks", json={"project_id": pid, "title": "t"}, headers=headers)
    assert t.status_code == 200
    tid = t.json()["task_id"]
    assert client.post(f"/tasks/{tid}/chat", json={"prompt": "hello"}, headers=headers).status_code == 200
    assert client.post(f"/tasks/{tid}/run", json={"prompt": "do"}, headers=headers).status_code == 200
    assert client.get(f"/tasks/{tid}/approvals", headers=headers).status_code == 200
    assert (
        client.post(
            f"/tasks/{tid}/approvals",
            json={"action": "request_changes", "feedback": "fix"},
            headers=headers,
        ).status_code
        == 200
    )


def test_terminal_exec_high_risk_gate_and_policy(tmp_path: Path):
    calls = []

    def exec_terminal(task_id: str, command: str, allow_high_risk: bool):
        calls.append((task_id, command, allow_high_risk))
        if command.startswith("git status"):
            return {"accepted": True}
        return {"accepted": False, "reason": "denied by terminal command policy"}

    api, client = _mk_client(tmp_path, terminal_executor=exec_terminal)
    headers = {"X-Desktop-Token": api.token}
    assert (
        client.post("/tasks/t1/terminal/exec", json={"command": "git status"}, headers=headers).status_code
        == 403
    )
    assert (
        client.post(
            "/tasks/t1/terminal/exec",
            json={"command": "rm -rf /", "allow_high_risk": True},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/tasks/t1/terminal/exec",
            json={"command": "git status --short", "allow_high_risk": True},
            headers=headers,
        ).status_code
        == 200
    )
    assert len(calls) == 2


def test_sse_stream_endpoint_requires_auth(tmp_path: Path):
    api, client = _mk_client(tmp_path)
    assert client.get("/events/stream").status_code == 401
