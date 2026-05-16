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
    assert deliverable.metadata["project_plan"]["status"] == "complete"
    assert [event.event for event in events] and all(
        event.event == CompanyEvent.LIFECYCLE for event in events
    )
    event_names = [event.payload["name"] for event in events]
    assert "delivery_plan_updated" in event_names
    assert "delivery_plan_created" in event_names
    assert "milestone_updated" in event_names
    assert "risk_identified" in event_names
    assert "ready_for_release" in event_names


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

    assert plan.status == "complete"
    assert "Engineering and QA artifacts are available" in plan.progress_summary
    assert any(m.name == "Release handoff prepared" for m in plan.milestones)
    assert plan.to_dict()["timeline"]["cadence"] == "daily async check-in until release"
    assert plan.to_dict()["weighted_completion"] == 100
    assert plan.to_dict()["key_dependencies"]
    assert "## Executive Summary" in plan.to_markdown()
    assert "## Risk Register" in plan.to_markdown()


def test_delivery_proactive_planning_tracks_early_phase_without_blocking(tmp_path):
    department = DeliveryDepartment(ProjectMemory(str(tmp_path)))
    task = make_task(
        payload={"engineering_result": None, "qa_report": None, "qa_metadata": {}},
        context={"project_phase": "prototyping"},
    )
    task.origin = "product"
    task.artifact_type = "prd"

    deliverable = asyncio.run(department.process(task))
    summary = deliverable.metadata["delivery_summary"]

    assert deliverable.status == "success"
    assert deliverable.metadata["ready_for_devops"] is False
    assert "handoff_to" not in deliverable.metadata
    assert summary["overall_status"] == "on_track"
    assert summary["completion_percentage"] < 100
    assert summary["next_milestone"] in {
        "Engineering implementation ready",
        "QA verification complete",
    }
    assert deliverable.metadata["critical_blockers"] == []


def test_delivery_health_assessment_exposes_blockers_and_summary(tmp_path):
    department = DeliveryDepartment(ProjectMemory(str(tmp_path)))
    task = make_task(payload={"qa_report": None, "qa_metadata": {}})
    plan = department._run_delivery_cycle(task)

    assert plan.overall_status == "delayed"
    assert plan.completion_percentage < 100
    assert plan.critical_blockers == ["qa_report"]
    assert plan.to_summary()["critical_blockers"] == ["qa_report"]
    assert "Critical blockers" in plan.to_markdown()


def test_delivery_handover_to_devops_requires_release_ready_plan(tmp_path):
    department = DeliveryDepartment(ProjectMemory(str(tmp_path)))
    deliverable = asyncio.run(department.process(make_task()))
    handover = deliverable.metadata["delivery_handover"]

    assert handover["ready_for_devops"] is True
    assert handover["project_name"] == "Invite Flow"
    assert handover["delivery_summary"]["completion_percentage"] == 100
    assert handover["delivery_summary"]["weighted_completion"] == 100
    assert handover["critical_blockers"] == []
    assert handover["go_no_go_recommendation"].startswith("GO")
    assert "Release scope" in handover["release_notes_draft"]
    assert "previous known-good" in handover["rollback_plan"]
