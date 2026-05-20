import asyncio
from pathlib import Path

from aider.company.daemon.runner import CompanyDaemonRunner
from aider.company.runtime import CompanyRunRequest, run_company_task
from aider.company.schemas import CompanyEvent, CompanyTask, Deliverable, EventMessage
from aider.company.tracker import TrackerIssue
from aider.integrations.discord import DiscordAiderBot, DiscordSessionKey
from aider.company.cli import run_company_cli_with_coder, CompanyCLICommand


class StubCoder:
    def __init__(self):
        self.runs = []
        self.io = type("IO", (), {"tool_output": lambda *args, **kwargs: None})()

    def run(self, with_message=None):
        self.runs.append(with_message)
        return "ok"


def test_run_company_task_contract():
    async def _execute(task, metadata):
        return {"task_id": task.task_id, "surface": metadata["surface"]}

    req = CompanyRunRequest(
        surface="test",
        session_id="s1",
        task=CompanyTask(
            task_id="t1", origin="ceo", target="engineering", artifact_type="raw_prompt", payload="hi", blocking=False
        ),
    )
    result = asyncio.run(run_company_task(req, execute=_execute))
    assert result == {"task_id": "t1", "surface": "test"}


def test_cli_uses_runtime_entrypoint(monkeypatch):
    called = {"count": 0}

    async def _fake_run(*args, **kwargs):
        called["count"] += 1
        return {"summary": "ok"}

    monkeypatch.setattr("aider.company.cli.run_company_task", _fake_run)
    coder = StubCoder()
    cmd = CompanyCLICommand(action="create", idea="x")
    run_company_cli_with_coder(cmd, coder)
    assert called["count"] == 1


def test_event_parity_cli_daemon_discord(monkeypatch, tmp_path):
    # CLI emits no bus events yet; runtime contract currently standardizes start calls.
    # Daemon should emit daemon_run_progress lifecycle events.
    events = []

    class OrchestratorStub:
        departments = {"engineering": object()}

        async def _emit(self, msg: EventMessage):
            events.append(msg.payload.get("name"))

    class COOStub:
        async def run_department_task(self, task):
            return Deliverable(
                task_id=task.task_id,
                department="engineering",
                artifact_type=task.artifact_type,
                payload="done",
                status="success",
                metadata={},
            )

    runner = CompanyDaemonRunner(orchestrator=OrchestratorStub(), coo=COOStub(), timeout_seconds=2)
    issue = TrackerIssue(identifier="I1", title="t", description="d", labels=(), url=None)
    workspace = Path(tmp_path)
    asyncio.run(runner.execute("prompt", workspace, issue))

    # Discord path should call runtime entrypoint.
    calls = {"count": 0}

    async def _fake_run(*args, **kwargs):
        calls["count"] += 1
        return {"summary": "ok"}

    monkeypatch.setattr("aider.integrations.discord.run_company_task", _fake_run)
    bot = DiscordAiderBot()
    key = DiscordSessionKey(guild_id=1, channel_id=2, user_id=3)

    async def _fake_session(*args, **kwargs):
        return StubCoder()

    monkeypatch.setattr(bot, "get_or_create_session", _fake_session)
    asyncio.run(bot.run_instruction(key=key, repo_path=str(tmp_path), user_id=3, prompt="hello"))

    assert calls["count"] == 1
    assert "daemon_run_progress" in events
