from __future__ import annotations

import asyncio
import json

from aider.company.departments.ux import UXDepartment
from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory


class FakeAgentLoop:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def run_structured(self, **kwargs):
        self.calls.append(kwargs)
        return self.outputs.pop(0)


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


def make_task() -> CompanyTask:
    return CompanyTask(
        task_id="ux-task-1",
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


def test_ux_validation_failed_retries_once_and_returns_gate_payload(tmp_path):
    agent_loop = FakeAgentLoop(
        [
            {"content": "not json"},
            {"content": json.dumps({"title": "Still incomplete"})},
        ]
    )
    department = UXDepartment(ProjectMemory(str(tmp_path)), agent_loop)

    deliverable = asyncio.run(department.process(make_task()))

    assert deliverable.status == "validation_failed"
    assert deliverable.department == "ux"
    assert deliverable.artifact_type == "design_spec"
    assert deliverable.metadata["ux_retry_count"] == 1
    assert deliverable.metadata["validation_errors"]
    assert "Engineering Schema Gate REJECTION" in deliverable.payload
    assert len(agent_loop.calls) == 2
    assert "Engineering Schema Gate REJECTION" in agent_loop.calls[1]["system_prompt"]


def test_ux_retry_can_recover_to_successful_design_spec(tmp_path):
    valid_spec = valid_design_spec()
    agent_loop = FakeAgentLoop(
        [
            {"content": "not json"},
            {"content": f"```json\n{json.dumps(valid_spec)}\n```"},
        ]
    )
    department = UXDepartment(ProjectMemory(str(tmp_path)), agent_loop)
    events = []

    async def capture_event(message):
        events.append(message)

    department._on_event = capture_event

    deliverable = asyncio.run(department.process(make_task()))

    assert deliverable.status == "success"
    assert deliverable.metadata["ux_retry_count"] == 1
    assert deliverable.metadata["schema_gate_approved"] is True
    assert (
        deliverable.metadata["design_spec_structured"]["title"] == valid_spec["title"]
    )
    assert "Screens: 1" in deliverable.metadata["design_spec_summary"]
    assert len(agent_loop.calls) == 2
    assert len(events) == 1
    assert events[0].payload["name"] == "ux_design_complete"
    assert events[0].payload["screens_count"] == 1
