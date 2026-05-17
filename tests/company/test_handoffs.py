from __future__ import annotations

import asyncio
import json

from aider.company.departments.delivery import DeliveryDepartment
from aider.company.departments.devops import DevOpsDepartment
from aider.company.departments.ux import UXDepartment
from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory


class FakeUXAgentLoop:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def run_structured(self, **kwargs):
        self.calls.append(kwargs)
        return self.outputs.pop(0)


def valid_delivery_task(**overrides) -> CompanyTask:
    payload = {
        "engineering_result": {"summary": "Implemented invite flow", "files": ["app.py"]},
        "engineering_metadata": {"files": ["app.py", "test_app.py"]},
        "qa_report": {"summary": "QA passed", "test_passed": True},
        "qa_metadata": {"test_coverage": "pytest"},
        "prd_content": "Ship a predictable invite flow.",
        "build_commands": ["python -m build"],
        "deployment_commands": [],
    }
    payload.update(overrides.pop("payload", {}))
    context = {
        "project_name": "Invite Flow",
        "prd_summary": "Admins can invite teammates safely.",
        "deployment_target": {"provider": "local", "environment": "staging"},
    }
    context.update(overrides.pop("context", {}))
    return CompanyTask(
        task_id="release-1",
        origin="qa",
        target="delivery",
        artifact_type="test_report",
        payload=payload,
        context=context,
        **overrides,
    )


def valid_design_spec() -> dict:
    return {
        "title": "Invite teammates",
        "overview": "A focused invite flow with recoverable loading and error states.",
        "screens": [
            {
                "name": "InviteScreen",
                "route": "/settings/team/invites",
                "description": "Lets admins invite teammates and inspect pending invitations.",
                "components_used": ["InviteForm"],
                "data_fetching": "Load pending invites from the team invites API.",
            }
        ],
        "components": [
            {
                "name": "InviteForm",
                "description": "Collects invitee email and role before submission.",
                "props": [
                    {
                        "field_name": "email",
                        "data_type": "string",
                        "source": "local_state",
                        "description": "Invitee email address.",
                        "validation_rules": ["Must be a valid email address"],
                    }
                ],
                "interaction_states": [
                    {
                        "state_name": "loading",
                        "trigger": "Invite submission starts",
                        "ui_change": "Disable submit and show spinner text.",
                    },
                    {
                        "state_name": "error",
                        "trigger": "Invite submission fails",
                        "ui_change": "Show inline validation and API error feedback.",
                    },
                ],
                "accessibility_notes": "Labels are programmatically associated with inputs.",
            }
        ],
        "global_state_management": "Keep form state local and cache pending invites globally.",
        "accessibility_checklist": {
            "keyboard_navigation": True,
            "screen_reader_labels": True,
            "color_contrast_aa": True,
            "aria_attributes": True,
            "focus_management": True,
            "notes": "Meets WCAG 2.1 AA for labels, focus, and contrast.",
        },
        "error_boundaries": "Wrap invite list and form submission in recoverable boundaries.",
    }


def ux_task() -> CompanyTask:
    return CompanyTask(
        task_id="ux-1",
        origin="product",
        target="ux",
        artifact_type="prd",
        payload={
            "prd_structured": {
                "title": "Invite teammates",
                "problem_statement": "Admins need to invite collaborators.",
                "acceptance_criteria": ["Admins can send invites"],
            }
        },
    )


def test_delivery_to_devops_handoff_allows_green_release(tmp_path):
    delivery = DeliveryDepartment(ProjectMemory(str(tmp_path)))
    delivery_deliverable = asyncio.run(delivery.process(valid_delivery_task()))
    handover = delivery_deliverable.metadata["delivery_handover"]

    assert delivery_deliverable.status == "success"
    assert handover["ready_for_devops"] is True

    devops = DevOpsDepartment(ProjectMemory(str(tmp_path)))

    async def fake_run(command: str, *, high_risk_allowed: bool = False) -> str:
        return f"$ {command}\nexit_code=0\nok"

    devops._run_shell = fake_run  # type: ignore[method-assign]
    devops_task = CompanyTask(
        task_id="release-1",
        origin="delivery",
        target="devops",
        artifact_type="deploy_request",
        payload={
            "delivery_handover": handover,
            "build_commands": ["python -m build"],
            "deployment_commands": [],
            "version": "1.2.3",
        },
        context={"project_name": "Invite Flow"},
    )

    devops_deliverable = asyncio.run(devops.process(devops_task))

    assert devops_deliverable.status == "success"
    assert devops_deliverable.metadata["delivery_handover"]["ready_for_devops"] is True
    assert devops_deliverable.metadata["handoff_to"] == "ceo"


def test_delivery_to_devops_handoff_rejects_missing_readiness(tmp_path):
    delivery = DeliveryDepartment(ProjectMemory(str(tmp_path)))
    delivery_deliverable = asyncio.run(
        delivery.process(valid_delivery_task(payload={"qa_report": None, "qa_metadata": {}}))
    )
    handover = delivery_deliverable.metadata["delivery_handover"]

    assert delivery_deliverable.status == "failure"
    assert handover["ready_for_devops"] is False
    assert "qa_report" in handover["critical_blockers"]

    devops = DevOpsDepartment(ProjectMemory(str(tmp_path)))
    devops_task = CompanyTask(
        task_id="release-1",
        origin="delivery",
        target="devops",
        artifact_type="deploy_request",
        payload={"delivery_handover": handover},
    )

    devops_deliverable = asyncio.run(devops.process(devops_task))

    assert devops_deliverable.status == "failure"
    assert devops_deliverable.metadata["handoff_to"] == "delivery"
    assert devops_deliverable.metadata["delivery_handover"]["ready_for_devops"] is False


def test_ux_design_spec_v2_gate_rejects_and_retry_recovers(tmp_path):
    invalid_spec = {"title": "Invite teammates", "overview": "Too thin"}
    repaired_spec = valid_design_spec()
    agent_loop = FakeUXAgentLoop(
        [
            {"content": json.dumps(invalid_spec)},
            {"content": json.dumps(repaired_spec)},
        ]
    )
    department = UXDepartment(ProjectMemory(str(tmp_path)), agent_loop)

    deliverable = asyncio.run(department.process(ux_task()))

    assert deliverable.status == "success"
    assert deliverable.metadata["schema_gate_approved"] is True
    assert deliverable.metadata["ux_retry_count"] == 1
    assert deliverable.metadata["design_spec_structured"]["title"] == "Invite teammates"
    assert len(agent_loop.calls) == 2
    assert "Engineering Schema Gate REJECTION" in agent_loop.calls[1]["system_prompt"]
