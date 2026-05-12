from __future__ import annotations

import asyncio
import json

from aider.company.departments.product import ProductDepartment
from aider.company.schemas import CompanyEvent, CompanyTask, EventMessage, PRD
from aider.integrations.discord import format_lifecycle_event_message
from aider.memory import ProjectMemory


class FakeAgentLoop:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def run_structured(self, **kwargs):
        self.calls.append(kwargs)
        return self.outputs.pop(0)


def valid_prd(title="Revised invites") -> dict:
    return {
        "title": title,
        "problem_statement": "Admins need a reliable way to invite teammates.",
        "goals": ["Let admins send invites with clear feedback."],
        "success_metrics": ["95% of valid invite submissions create an invite event."],
        "user_stories": [
            "As an admin, I want to invite a teammate so that they can join my workspace.",
            "As an admin, I want failed invites explained so that I can fix input errors.",
        ],
        "acceptance_criteria": [
            "Given a valid email, when the admin submits the form, then an invite is created.",
            "Given an invalid email, when the admin submits, then inline validation is shown.",
        ],
        "technical_considerations": ["Reuse the existing team invite API."],
        "out_of_scope": ["Bulk invites are not included in MVP."],
        "priority": "MVP",
        "open_questions": [],
    }


def test_prd_revision_injects_previous_prd_feedback_playbook_and_emits_events(tmp_path):
    revised = valid_prd()
    agent_loop = FakeAgentLoop(
        [
            {"content": json.dumps(revised)},
            {"content": json.dumps({"issues": [], "improved_prd": None})},
        ]
    )
    department = ProductDepartment(ProjectMemory(str(tmp_path)), agent_loop)
    events = []

    async def capture(event):
        events.append(event)

    department._on_event = capture
    previous_prd = PRD(
        title="Invite teammates",
        problem_statement="Admins cannot invite teammates from settings.",
        acceptance_criteria=["Given an email, when submitted, then send an invite."],
    )
    task = CompanyTask(
        task_id="prd-1",
        origin="ceo",
        target="product",
        artifact_type="prd",
        payload={
            "original_request": "Add team invites.",
            "previous_prd": previous_prd.to_markdown(),
            "previous_prd_structured": previous_prd.to_dict(),
            "ceo_feedback": "Add clear validation handling and keep MVP scoped.",
            "reviewer_notes": "Acceptance criteria should mention invalid email behavior.",
            "revision_count": 1,
        },
        context={"playbook_guidance": ["Prefer measurable launch criteria."]},
    )

    deliverable = asyncio.run(department.process(task))

    revision_prompt = agent_loop.calls[0]["task"]
    assert "Original request:\nAdd team invites." in revision_prompt
    assert "Previous PRD (structured JSON):" in revision_prompt
    assert "Previous PRD (markdown):" in revision_prompt
    assert (
        "CEO / clarification feedback:\nAdd clear validation handling"
        in revision_prompt
    )
    assert "Reviewer notes:\nAcceptance criteria" in revision_prompt
    assert (
        "Relevant playbook guidance:\n- Prefer measurable launch criteria."
        in revision_prompt
    )
    assert agent_loop.calls[1]["task"].find("invalid email") >= 0
    assert deliverable.metadata["revision_count"] == 1
    assert (
        deliverable.metadata["ceo_feedback"]
        == "Add clear validation handling and keep MVP scoped."
    )
    assert deliverable.metadata["previous_prd_summary"].startswith("Invite teammates")
    assert deliverable.metadata["prd_structured"]["revision_count"] == 1
    assert [event.payload["name"] for event in events] == [
        "product_revision_start",
        "product_prd_revised",
    ]


def test_discord_formats_product_revision_lifecycle_message():
    event = EventMessage(
        event=CompanyEvent.LIFECYCLE,
        task_id="prd-1",
        payload={"name": "product_revision_start", "revision_count": 2},
    )

    message = format_lifecycle_event_message(event)

    assert "Product is revising PRD based on feedback" in message
    assert "Task: `prd-1`" in message
