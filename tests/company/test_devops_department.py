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
