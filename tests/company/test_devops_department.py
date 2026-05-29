from __future__ import annotations

import asyncio

from aider.company.departments.devops import DevOpsDepartment
from aider.company.schemas import (
    BuildArtifact,
    CompanyTask,
    DeliveryHandover,
    DeploymentResult,
)
from aider.memory import ProjectMemory


def ready_handover() -> dict:
    return {
        "project_name": "Invite Flow",
        "ready_for_devops": True,
        "delivery_summary": {
            "completion_percentage": 100,
            "overall_status": "complete",
        },
        "release_scope": "Ship invites",
        "critical_blockers": [],
        "rollback_plan": "Rollback to previous known-good artifact.",
        "environment": "staging",
    }


def make_task(tmp_path, **payload_overrides):
    payload = {
        "delivery_handover": ready_handover(),
        "build_commands": ["python -m build"],
        "deployment_commands": [],
        "version": "2.1.0",
    }
    payload.update(payload_overrides)
    return CompanyTask(
        task_id="release-1",
        origin="delivery",
        target="devops",
        artifact_type="deploy_request",
        payload=payload,
        context={
            "project_name": "Invite Flow",
            "playbook_guidance": ["Use rollout checklist."],
        },
    )


def test_devops_builds_and_deploys_valid_delivery_handover(tmp_path):
    department = DevOpsDepartment(ProjectMemory(str(tmp_path)))
    events = []

    async def capture(event):
        events.append(event)

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        return f"$ {command}\nexit_code=0\nok"

    department._on_event = capture
    department._run_shell = fake_run  # type: ignore[method-assign]

    deliverable = asyncio.run(department.process(make_task(tmp_path)))

    assert deliverable.status == "success"
    assert deliverable.payload["build_artifact"]["artifact_type"] == "pypi_package"
    assert deliverable.payload["build_artifact"]["tag"] == "v2.1.0"
    assert deliverable.payload["deployment_result"]["status"] == "success"
    assert deliverable.payload["deployment_result"]["environment"] == "staging"
    assert deliverable.metadata["handoff_to"] == "ceo"
    event_names = [event.payload["name"] for event in events]
    assert "devops_build_started" in event_names
    assert "devops_build_success" in event_names
    assert "devops_deployed" in event_names


def test_devops_refuses_unready_delivery_handover(tmp_path):
    department = DevOpsDepartment(ProjectMemory(str(tmp_path)))
    blocked = ready_handover()
    blocked["ready_for_devops"] = False
    blocked["critical_blockers"] = ["qa_report"]
    task = make_task(tmp_path, delivery_handover=blocked)

    deliverable = asyncio.run(department.process(task))

    assert deliverable.status == "failure"
    assert deliverable.metadata["handoff_to"] == "delivery"
    assert deliverable.metadata["delivery_handover"]["critical_blockers"] == [
        "qa_report"
    ]


def test_devops_blocks_high_risk_deploy_without_approval(tmp_path):
    department = DevOpsDepartment(ProjectMemory(str(tmp_path)))

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        return f"$ {command}\nexit_code=0\nok"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(tmp_path, deployment_commands=["kubectl apply -f k8s.yaml"])

    deliverable = asyncio.run(department.process(task))

    assert deliverable.status == "failure"
    assert "High-risk DevOps command requires approval" in deliverable.payload["error"]


def test_devops_schema_round_trips_build_and_deployment_result():
    artifact = BuildArtifact(
        artifact_type="docker_image",
        name="invite-flow",
        version="1.0.0",
        tag="v1.0.0",
        location="invite-flow:v1.0.0",
        build_logs_summary="ok",
    )
    deployment = DeploymentResult(
        environment="production",
        status="success",
        deployed_url="https://invite-flow.example.com",
        rollback_url="rollback://previous",
        build_artifact=artifact,
        deployment_notes="Manual production smoke passed.",
        deployed_at="2026-05-17T00:00:00+00:00",
        deployment_logs="deployed",
    )
    handover = DeliveryHandover.from_dict(ready_handover())

    assert BuildArtifact.from_dict(artifact.to_dict()).to_dict() == artifact.to_dict()
    assert (
        DeploymentResult.from_dict(deployment.to_dict()).to_dict()
        == deployment.to_dict()
    )
    assert handover.ready_for_devops is True
    assert handover.environment == "staging"


def test_devops_uses_configurable_fallback_and_captures_logs(tmp_path):
    from aider.company.config import DepartmentConfig

    department = DevOpsDepartment(
        ProjectMemory(str(tmp_path)),
        config=DepartmentConfig(
            name="devops",
            devops_build_fallback_commands=["python -m build"],
            devops_retry_base_delay=0,
        ),
    )

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        return f"$ {command}\nexit_code=0\nfallback build ok"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(tmp_path, build_commands=[])
    task.payload.pop("build_commands")

    deliverable = asyncio.run(department.process(task))

    build = deliverable.payload["build_artifact"]
    assert build["detected_command_source"] == "fallback_configured"
    assert build["log_artifacts"]
    assert "fallback build ok" in build["build_logs_summary"]
    assert all(
        (tmp_path in __import__("pathlib").Path(path).parents)
        for path in build["log_artifacts"]
    )
    assert deliverable.payload["log_artifacts"]


def test_devops_retries_transient_build_failure(tmp_path):
    from aider.company.config import DepartmentConfig

    department = DevOpsDepartment(
        ProjectMemory(str(tmp_path)),
        config=DepartmentConfig(
            name="devops",
            devops_retry_attempts=3,
            devops_retry_base_delay=0,
        ),
    )
    calls = {"count": 0}
    events = []

    async def capture(event):
        events.append(event)

    async def flaky_run(command: str, *, high_risk_allowed: bool = False) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            return f"$ {command}\nexit_code=1\nconnection reset by peer"
        return f"$ {command}\nexit_code=0\nok after retry"

    department._on_event = capture
    department._run_shell = flaky_run  # type: ignore[method-assign]

    deliverable = asyncio.run(department.process(make_task(tmp_path)))

    assert deliverable.status == "success"
    assert calls["count"] == 2
    assert "ok after retry" in deliverable.payload["build_logs_summary"]
    assert "devops_command_retry" in [event.payload["name"] for event in events]


def test_devops_generates_vercel_command_with_approval(tmp_path):
    commands = []
    department = DevOpsDepartment(ProjectMemory(str(tmp_path)))

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append((command, high_risk_allowed))
        return f"$ {command}\nexit_code=0\nvercel deployed"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "vercel",
            "environment": "production",
            "config": {"project": "invite-flow", "scope": "acme"},
        },
        devops_high_risk_approved=True,
    )

    deliverable = asyncio.run(department.process(task))

    assert deliverable.status == "success"
    deployment = deliverable.payload["deployment_result"]
    assert deployment["target"]["provider"] == "vercel"
    assert deployment["deployed_url"] == "https://invite-flow.vercel.app"
    assert deployment["rollback_command"] == "vercel rollback"
    assert commands[-1] == ("vercel deploy --yes --prod --scope acme", True)


def test_devops_provider_deploy_requires_approval(tmp_path):
    department = DevOpsDepartment(ProjectMemory(str(tmp_path)))

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        return f"$ {command}\nexit_code=0\nshould not deploy"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "railway",
            "environment": "staging",
            "config": {"service": "web"},
        },
    )

    deliverable = asyncio.run(department.process(task))

    assert deliverable.status == "failure"
    assert "High-risk DevOps command requires approval" in deliverable.payload["error"]
    assert (
        "railway up --detach --service web --environment staging"
        in deliverable.payload["error"]
    )


def test_devops_blocks_disabled_provider(tmp_path):
    from aider.company.config import DepartmentConfig

    department = DevOpsDepartment(
        ProjectMemory(str(tmp_path)),
        config=DepartmentConfig(
            name="devops",
            devops_deployment_providers=["local", "vercel"],
        ),
    )

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        return f"$ {command}\nexit_code=0\nok"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "fly",
            "environment": "production",
            "config": {},
        },
        devops_high_risk_approved=True,
    )

    deliverable = asyncio.run(department.process(task))

    assert deliverable.status == "failure"
    assert "Deployment provider 'fly' is not enabled" in deliverable.payload["error"]


def test_devops_logs_url_uses_configured_artifact_upload_target(tmp_path):
    from aider.company.config import DepartmentConfig

    department = DevOpsDepartment(
        ProjectMemory(str(tmp_path)),
        config=DepartmentConfig(
            name="devops",
            devops_artifact_upload_target="s3://aider-deploy-logs/releases",
        ),
    )

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        return f"$ {command}\nexit_code=0\nok"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(tmp_path)

    deliverable = asyncio.run(department.process(task))

    deployment = deliverable.payload["deployment_result"]
    assert deployment["logs_url"].startswith("s3://aider-deploy-logs/releases/local/")
    assert deliverable.payload["logs_url"] == deployment["logs_url"]


def test_devops_merges_environment_specific_deployment_config(tmp_path):
    commands = []
    department = DevOpsDepartment(ProjectMemory(str(tmp_path)))

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append(command)
        return f"$ {command}\nexit_code=0\nrailway deployed"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "railway",
            "environment": "staging",
            "config": {
                "service": "default-web",
                "approval_level": "critical",
                "environments": {
                    "staging": {
                        "service": "staging-web",
                        "approval_level": "standard",
                    },
                    "production": {
                        "service": "prod-web",
                        "approval_level": "critical",
                    },
                },
            },
        },
        deployment_approvals={"staging": True},
    )

    deliverable = asyncio.run(department.process(task))

    assert deliverable.status == "success"
    deployment = deliverable.payload["deployment_result"]
    assert deployment["target"]["config"]["service"] == "staging-web"
    assert deployment["deployment_notes"].endswith("approval_level=standard.")
    assert (
        commands[-1]
        == "railway up --detach --service staging-web --environment staging"
    )


def test_devops_critical_environment_requires_environment_approval(tmp_path):
    department = DevOpsDepartment(ProjectMemory(str(tmp_path)))

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        return f"$ {command}\nexit_code=0\nshould not deploy"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "vercel",
            "environment": "production",
            "config": {"project": "invite-flow", "approval_level": "critical"},
        },
        deploy_approved=True,
    )

    deliverable = asyncio.run(department.process(task))

    assert deliverable.status == "failure"
    assert (
        "Production DevOps deployment requires approval" in deliverable.payload["error"]
    )


def test_devops_mocked_vercel_deploy_end_to_end_records_observability(tmp_path):
    department = DevOpsDepartment(ProjectMemory(str(tmp_path)))
    commands = []
    events = []

    async def capture(event):
        events.append(event)

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append((command, high_risk_allowed))
        if command.startswith("vercel"):
            return f"$ {command}\nexit_code=0\nhttps://invite-flow.vercel.app"
        return f"$ {command}\nexit_code=0\nbuild ok"

    department._on_event = capture
    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "vercel",
            "environment": "production",
            "config": {
                "project": "invite-flow",
                "logs_url": "https://vercel.example/logs/123",
            },
        },
        devops_production_approved=True,
        deployment_notes="Release train 2026.05 production deploy.",
    )

    deliverable = asyncio.run(department.process(task))

    assert deliverable.status == "success"
    assert commands == [
        ("python -m build", False),
        ("vercel deploy --yes --prod", True),
    ]
    deployment = deliverable.payload["deployment_result"]
    assert deployment["deployed_at"]
    assert deployment["deployment_notes"] == "Release train 2026.05 production deploy."
    assert deployment["logs_url"] == "https://vercel.example/logs/123"
    assert "devops_deployed" in [event.payload["name"] for event in events]
