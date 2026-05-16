from __future__ import annotations

import asyncio

from aider.company.departments.delivery import DeliveryDepartment
from aider.company.schemas import CompanyEvent, CompanyTask, ProjectPlan
from aider.memory import ProjectMemory


class FakeAgentLoop:
    def __init__(self):
        self.model = None


def make_task(**overrides):
    payload = {
        "engineering_result": {"summary": "Implemented feature", "files": ["app.py"]},
        "engineering_metadata": {"files": ["app.py", "test_app.py"]},
        "qa_report": {"summary": "QA passed", "test_passed": True},
        "qa_metadata": {"test_coverage": "executed"},
        "prd_content": "Ship a predictable invite flow.",
    }
    payload.update(overrides.pop("payload", {}))
    context = {
        "project_name": "Invite Flow",
        "prd_summary": "Admins can invite teammates safely.",
        "design_spec_summary": "Accessible invite form.",
        "skill_guidance": ["Use delivery launch checklist."],
        "playbook_guidance": ["Coordinate release risks before DevOps."],
    }
    context.update(overrides.pop("context", {}))
    return CompanyTask(
        task_id="delivery-1",
        origin="qa",
        target="delivery",
        artifact_type="test_report",
        payload=payload,
        context=context,
        **overrides,
    )


def test_delivery_plan_creation_emits_lifecycle_events(tmp_path):
    department = DeliveryDepartment(ProjectMemory(str(tmp_path)), FakeAgentLoop())
    events = []

    async def capture(event):
        events.append(event)

    department._on_event = capture

    deliverable = asyncio.run(department.process(make_task()))

    assert deliverable.status == "success"
    assert deliverable.artifact_type == "delivery_plan"
    assert "# Delivery Plan: Invite Flow" in deliverable.payload
    assert deliverable.metadata["handoff_to"] == "devops"
    assert deliverable.metadata["project_plan"]["status"] == "release_ready"
    assert [event.event for event in events] and all(
        event.event == CompanyEvent.LIFECYCLE for event in events
    )
    event_names = [event.payload["name"] for event in events]
    assert "delivery_plan_created" in event_names
    assert "milestone_updated" in event_names
    assert "risk_identified" in event_names


def test_delivery_risk_assessment_identifies_blocker_for_missing_qa(tmp_path):
    department = DeliveryDepartment(ProjectMemory(str(tmp_path)))
    events = []

    async def capture(event):
        events.append(event)

    department._on_event = capture
    task = make_task(payload={"qa_report": None, "qa_metadata": {}})

    deliverable = asyncio.run(department.process(task))

    assert deliverable.status == "failure"
    assert deliverable.metadata["handoff_to"] == "engineering"
    assert deliverable.metadata["high_risk_count"] == 1
    risks = deliverable.metadata["risks"]
    assert risks[0]["risk_id"] == "RISK-QA-EVIDENCE"
    assert risks[0]["blockers"] == ["qa_report"]
    assert "delivery_blocker" in [event.payload["name"] for event in events]


def test_delivery_progress_updates_release_ready_and_round_trips_schema(tmp_path):
    department = DeliveryDepartment(ProjectMemory(str(tmp_path)))
    deliverable = asyncio.run(department.process(make_task()))

    plan = ProjectPlan.from_dict(deliverable.metadata["project_plan"])

    assert plan.status == "release_ready"
    assert "Engineering and QA artifacts are available" in plan.progress_summary
    assert any(m.name == "Release handoff prepared" for m in plan.milestones)
    assert (
        plan.to_dict()["timeline"]["cadence"]
        == "daily async check-in until release"
    )
    assert "## Risk Register" in plan.to_markdown()
