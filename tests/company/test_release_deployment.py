from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aider.company.departments.devops import DevOpsDepartment
from aider.company.schemas import (
    BuildArtifact,
    CompanyTask,
    DeliveryHandover,
    DeploymentResult,
    DeploymentTarget,
)
from aider.memory import ProjectMemory


class FakeAgentLoop:
    """Minimal department loop stand-in matching existing Company test patterns."""

    def __init__(self):
        self.calls = []

    async def run(self, prompt: str, **kwargs):
        self.calls.append((prompt, kwargs))
        return ""


class FakeApprovalManager:
    """Tiny approval manager shim for high-risk deployment gate tests."""

    def __init__(self, approved: bool):
        self.approved = approved
        self.requests = []

    async def request_deployment(self, command: str, environment: str) -> bool:
        self.requests.append((command, environment))
        return self.approved


def ready_handover(**overrides) -> dict:
    handover = {
        "project_name": "Release Seam",
        "ready_for_devops": True,
        "delivery_summary": {
            "completion_percentage": 100,
            "overall_status": "complete",
            "next_milestone": "production-smoke",
        },
        "release_scope": "Ship release seam hardening.",
        "critical_blockers": [],
        "rollback_plan": "Rollback to the previous stable deployment.",
        "rollback_notes": ["Confirm previous artifact before cutting traffic."],
        "environment": "staging",
    }
    handover.update(overrides)
    return handover


def make_task(tmp_path, **payload_overrides) -> CompanyTask:
    payload = {
        "delivery_handover": ready_handover(),
        "build_commands": ["python -m build"],
        "deployment_commands": [],
        "version": "3.2.1",
    }
    payload.update(payload_overrides)
    return CompanyTask(
        task_id="release-seam-1",
        origin="delivery",
        target="devops",
        artifact_type="deploy_request",
        payload=payload,
        context={
            "project_name": "Release Seam",
            "playbook_guidance": ["Use the release checklist."],
            "skill_guidance": ["Capture deployment metadata for dashboards."],
        },
    )


def run(coro):
    return asyncio.run(coro)


def make_department(tmp_path):
    return DevOpsDepartment(ProjectMemory(str(tmp_path)), FakeAgentLoop())


def test_delivery_handover_to_devops_success_path_records_dashboard_metadata(tmp_path):
    department = make_department(tmp_path)
    events = []
    commands = []

    async def capture(event):
        events.append(event)

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append((command, high_risk_allowed))
        return f"$ {command}\nexit_code=0\nrelease seam ok"

    department._on_event = capture
    department._run_shell = fake_run  # type: ignore[method-assign]

    deliverable = run(department.process(make_task(tmp_path)))

    assert deliverable.status == "success"
    assert commands == [("python -m build", False)]
    assert deliverable.payload["release_artifact"] == str(tmp_path / "dist")
    assert deliverable.payload["git_tag"] == "v3.2.1"
    assert deliverable.payload["environment"] == "staging"
    assert deliverable.payload["deployment_result"]["status"] == "success"
    assert (
        deliverable.payload["deployment_result"]["rollback_url"]
        == "rollback://delivery-plan"
    )
    assert deliverable.metadata["handoff_to"] == "ceo"
    assert deliverable.metadata["blocking"] is False
    assert (
        deliverable.metadata["build_artifact"] == deliverable.payload["build_artifact"]
    )
    assert (
        deliverable.metadata["deployment_result"]
        == deliverable.payload["deployment_result"]
    )
    assert deliverable.payload["log_artifacts"]
    assert all(Path(path).exists() for path in deliverable.payload["log_artifacts"])
    event_names = [event.payload["name"] for event in events]
    assert event_names == [
        "devops_build_started",
        "devops_build_success",
        "devops_deploy_started",
        "devops_deploy_preview",
        "devops_deployed",
    ]


def test_delivery_readiness_gate_blocks_unready_handover_before_build(tmp_path):
    department = make_department(tmp_path)
    calls = []
    blocked = ready_handover(
        ready_for_devops=False,
        critical_blockers=["qa_signoff", "release_notes"],
        go_no_go_recommendation="NO-GO until QA signs off.",
    )

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        calls.append(command)
        return f"$ {command}\nexit_code=0\nshould not run"

    department._run_shell = fake_run  # type: ignore[method-assign]

    deliverable = run(
        department.process(make_task(tmp_path, delivery_handover=blocked))
    )

    assert deliverable.status == "failure"
    assert (
        deliverable.payload["summary"]
        == "DevOps release blocked by Delivery readiness gate."
    )
    assert deliverable.metadata["blocking"] is True
    assert deliverable.metadata["handoff_to"] == "delivery"
    assert deliverable.metadata["delivery_handover"]["critical_blockers"] == [
        "qa_signoff",
        "release_notes",
    ]
    assert calls == []


@pytest.mark.parametrize(
    ("marker", "expected_commands", "artifact_type", "source"),
    [
        (
            "Dockerfile",
            ["docker build -t release-seam:v3.2.1 ."],
            "docker_image",
            "auto_detected:Dockerfile",
        ),
        (
            "pyproject.toml",
            ["python -m build"],
            "pypi_package",
            "auto_detected:pyproject.toml",
        ),
        (
            "package.json",
            ["npm run build"],
            "static_site",
            "auto_detected:package.json",
        ),
    ],
)
def test_safe_build_command_detection_and_execution_for_project_types(
    tmp_path, marker, expected_commands, artifact_type, source
):
    if marker == "Dockerfile":
        (tmp_path / marker).write_text("FROM scratch\n", encoding="utf-8")
    elif marker == "pyproject.toml":
        (tmp_path / marker).write_text(
            "[build-system]\nrequires=[]\n", encoding="utf-8"
        )
    else:
        (tmp_path / marker).write_text(
            json.dumps({"scripts": {"build": "vite build"}}), encoding="utf-8"
        )

    department = make_department(tmp_path)
    commands = []

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append((command, high_risk_allowed))
        return f"$ {command}\nexit_code=0\nbuild ok"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(tmp_path, build_commands=[])
    task.payload.pop("build_commands")

    deliverable = run(department.process(task))

    assert deliverable.status == "success"
    assert [command for command, _ in commands] == expected_commands
    assert all(high_risk is False for _, high_risk in commands)
    assert deliverable.payload["build_artifact"]["artifact_type"] == artifact_type
    assert deliverable.payload["build_artifact"]["detected_command_source"] == source
    assert "build ok" in deliverable.payload["build_logs_summary"]


@pytest.mark.parametrize(
    "deployment_commands",
    [
        ["vercel deploy --prod"],
        ["docker push registry.example.com/release-seam:v3.2.1"],
    ],
)
def test_high_risk_deployment_commands_require_approval_and_denial_blocks(
    tmp_path, deployment_commands
):
    department = make_department(tmp_path)
    approval_manager = FakeApprovalManager(approved=False)
    executed = []

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        if command in deployment_commands:
            approved = await approval_manager.request_deployment(command, "staging")
            department._validate_command(command, high_risk_allowed=approved)
        else:
            department._validate_command(command, high_risk_allowed=high_risk_allowed)
        executed.append(command)
        return f"$ {command}\nexit_code=0\nok"

    department._run_shell = fake_run  # type: ignore[method-assign]

    deliverable = run(
        department.process(make_task(tmp_path, deployment_commands=deployment_commands))
    )

    assert deliverable.status == "failure"
    assert "High-risk DevOps command requires approval" in deliverable.payload["error"]
    assert approval_manager.requests == [(deployment_commands[0], "staging")]
    assert executed == ["python -m build"]


def test_approved_high_risk_provider_command_executes(tmp_path):
    department = make_department(tmp_path)
    approval_manager = FakeApprovalManager(approved=True)
    commands = []

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        if command.startswith("vercel"):
            high_risk_allowed = await approval_manager.request_deployment(
                command, "production"
            )
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append((command, high_risk_allowed))
        return f"$ {command}\nexit_code=0\nprovider ok"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "vercel",
            "environment": "production",
            "config": {"project": "release-seam"},
        },
        devops_production_approved=True,
    )

    deliverable = run(department.process(task))

    assert deliverable.status == "success"
    assert approval_manager.requests == [("vercel deploy --yes --prod", "production")]
    assert commands == [
        ("python -m build", False),
        ("vercel deploy --yes --prod", True),
    ]
    assert deliverable.payload["deploy_url"] == "https://release-seam.vercel.app"


def test_build_failure_stops_before_deploy_and_returns_blocking_failure(tmp_path):
    department = make_department(tmp_path)
    commands = []

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        commands.append(command)
        if command == "python -m build":
            return f"$ {command}\nexit_code=1\nwheel build failed"
        return f"$ {command}\nexit_code=0\nshould not deploy"

    department._run_shell = fake_run  # type: ignore[method-assign]

    deliverable = run(
        department.process(
            make_task(
                tmp_path,
                deployment_commands=["vercel deploy --prod"],
                devops_high_risk_approved=True,
            )
        )
    )

    assert deliverable.status == "failure"
    assert deliverable.metadata["blocking"] is True
    assert deliverable.metadata["handoff_to"] == "engineering"
    assert "wheel build failed" in deliverable.payload["error"]
    assert commands == ["python -m build"]


def test_deployment_result_and_build_artifact_round_trip_dashboard_metadata():
    artifact = BuildArtifact(
        artifact_type="docker_image",
        name="release-seam",
        version="3.2.1",
        tag="v3.2.1",
        location="registry.example.com/release-seam:v3.2.1",
        build_logs_summary="docker build ok",
        log_artifacts=["/tmp/build.log"],
        detected_command_source="auto_detected:Dockerfile",
    )
    target = DeploymentTarget(
        provider="vercel",
        environment="production",
        config={"project": "release-seam", "approval_level": "critical"},
    )
    result = DeploymentResult(
        environment="production",
        status="partial",
        target=target,
        deployed_url="https://release-seam.vercel.app",
        logs_url="https://logs.example.com/release-seam",
        rollback_url="rollback://delivery-plan",
        rollback_command="vercel rollback",
        deployment_notes="Build passed; production smoke pending.",
        deployed_at="2026-05-17T00:00:00+00:00",
        build_artifact=artifact,
        deployment_logs="provider accepted deployment",
        log_artifacts=["/tmp/deploy.log"],
    )

    artifact_round_trip = BuildArtifact.from_dict(artifact.to_dict())
    result_round_trip = DeploymentResult.from_dict(result.to_dict())

    assert artifact_round_trip.to_dict() == artifact.to_dict()
    assert result_round_trip.to_dict() == result.to_dict()
    dashboard_metadata = {
        "build_artifact": artifact_round_trip.to_dict(),
        "deployment_result": result_round_trip.to_dict(),
        "artifact_links": [artifact_round_trip.location],
        "logs_url": result_round_trip.logs_url,
        "rollback_command": result_round_trip.rollback_command,
    }
    assert (
        dashboard_metadata["build_artifact"]["detected_command_source"]
        == "auto_detected:Dockerfile"
    )
    assert dashboard_metadata["deployment_result"]["status"] == "partial"
    assert dashboard_metadata["rollback_command"] == "vercel rollback"


def test_rollback_command_generation_and_logging_for_provider_deploy(tmp_path):
    department = make_department(tmp_path)
    events = []

    async def capture(event):
        events.append(event)

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        return f"$ {command}\nexit_code=0\nrolled forward with rollback metadata"

    department._on_event = capture
    department._run_shell = fake_run  # type: ignore[method-assign]
    handover = ready_handover(
        environment="production",
        deployment_target={
            "provider": "fly",
            "environment": "production",
            "config": {"app": "release-seam"},
        },
    )
    task = make_task(
        tmp_path,
        delivery_handover=handover,
        deployment_commands=[],
        devops_production_approved=True,
    )

    deliverable = run(department.process(task))

    assert deliverable.status == "success"
    deployment = deliverable.payload["deployment_result"]
    assert deployment["rollback_command"] == "flyctl releases rollback"
    assert deliverable.payload["rollback_command"] == "flyctl releases rollback"
    assert deployment["rollback_url"] == "rollback://delivery-plan"
    assert "approval_level=high" in deployment["deployment_notes"]
    assert deployment["log_artifacts"]
    assert "rolled forward" in Path(deployment["log_artifacts"][0]).read_text(
        encoding="utf-8"
    )
    deployed_events = [
        event for event in events if event.payload["name"] == "devops_deployed"
    ]
    assert deployed_events
    assert (
        deployed_events[0].payload["deployment"]["rollback_command"]
        == "flyctl releases rollback"
    )


@pytest.mark.parametrize(
    ("provider", "config", "expected_command", "expected_rollback"),
    [
        (
            "netlify",
            {"site": "release-seam", "dir": "dist"},
            "netlify deploy --dir dist --prod --site release-seam",
            "netlify rollback",
        ),
        (
            "kubernetes",
            {"manifest": "deploy.yaml", "namespace": "prod", "deployment": "web"},
            "kubectl apply -f deploy.yaml --namespace prod",
            "kubectl rollout undo deployment/web --namespace prod",
        ),
    ],
)
def test_additional_providers_generate_preview_and_rollback_metadata(
    tmp_path, provider, config, expected_command, expected_rollback
):
    department = make_department(tmp_path)
    commands = []

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append((command, high_risk_allowed))
        return f"$ {command}\nexit_code=0\nprovider deployed"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": provider,
            "environment": "production",
            "config": config,
        },
        previous_artifact="release-seam:v3.2.0",
        rollback_owner="Release Captain",
        rollback_validation_steps=["Run production smoke", "Confirm error budget"],
        devops_production_approved=True,
    )

    deliverable = run(department.process(task))

    assert deliverable.status == "success"
    assert commands[-1] == (expected_command, True)
    deployment = deliverable.payload["deployment_result"]
    assert deployment["dry_run_preview"]["commands"] == [expected_command]
    assert deployment["dry_run_preview"]["approval_required"] is True
    assert deployment["rollback_command"] == expected_rollback
    assert deployment["rollback_metadata"]["previous_artifact"] == "release-seam:v3.2.0"
    assert deployment["rollback_metadata"]["owner"] == "Release Captain"
    assert deployment["rollback_metadata"]["validation_steps"] == [
        "Run production smoke",
        "Confirm error budget",
    ]


def test_production_provider_deploy_blocks_before_command_without_approval(tmp_path):
    department = make_department(tmp_path)
    commands = []

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append(command)
        return f"$ {command}\nexit_code=0\nshould not deploy"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "netlify",
            "environment": "production",
            "config": {"site": "release-seam", "dir": "dist"},
        },
    )

    deliverable = run(department.process(task))

    assert deliverable.status == "failure"
    assert (
        "Production DevOps deployment requires approval" in deliverable.payload["error"]
    )
    assert commands == ["python -m build"]


def test_aws_s3_dry_run_preview_is_human_readable_and_safety_gated(tmp_path):
    department = make_department(tmp_path)
    commands = []

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append(command)
        return f"$ {command}\nexit_code=0\nbuild only"

    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "aws",
            "environment": "production",
            "config": {"s3_bucket": "release-seam-prod", "source": "dist"},
        },
        previous_artifact="s3://release-seam-prod/previous",
        rollback_owner="Release Captain",
        rollback_validation_steps=["Check CloudFront health", "Run smoke tests"],
        devops_dry_run=True,
    )

    deliverable = run(department.process(task))

    assert deliverable.status == "success"
    assert commands == ["python -m build"]
    deployment = deliverable.payload["deployment_result"]
    preview = deployment["dry_run_preview"]
    assert preview["commands"] == ["aws s3 sync dist s3://release-seam-prod --delete"]
    assert preview["will_execute"] is False
    assert (
        preview["approval_gate"]
        == "Approval is required before executing aws/production."
    )
    assert "Deploy release-seam:v3.2.1 to aws/production" in preview["human_summary"]
    assert "Collect approval before any provider side effects" in " ".join(
        preview["steps"]
    )
    assert preview["rollback_summary"].startswith("Rollback owner: Release Captain")
    assert preview["rollback"]["previous_artifact"] == "s3://release-seam-prod/previous"


def test_deployment_dry_run_preview_skips_provider_commands_without_approval(tmp_path):
    department = make_department(tmp_path)
    commands = []
    events = []

    async def capture(event):
        events.append(event)

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        department._validate_command(command, high_risk_allowed=high_risk_allowed)
        commands.append(command)
        return f"$ {command}\nexit_code=0\nbuild only"

    department._on_event = capture
    department._run_shell = fake_run  # type: ignore[method-assign]
    task = make_task(
        tmp_path,
        deployment_commands=[],
        deployment_target={
            "provider": "cloudflare-pages",
            "environment": "production",
            "config": {"project": "release-seam", "dir": "public"},
        },
        devops_dry_run=True,
    )

    deliverable = run(department.process(task))

    assert deliverable.status == "success"
    deployment = deliverable.payload["deployment_result"]
    assert deployment["status"] == "partial"
    assert deployment["dry_run_preview"]["will_execute"] is False
    assert deployment["dry_run_preview"]["approval_required"] is True
    assert deployment["dry_run_preview"]["commands"] == [
        "wrangler pages deploy public --project-name release-seam --branch production"
    ]
    assert (
        deployment["deployment_logs"]
        == "Dry-run preview only; no deployment commands executed."
    )
    assert commands == ["python -m build"]
    assert "devops_deploy_dry_run" in [event.payload["name"] for event in events]
