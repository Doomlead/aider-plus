from aider.company.schemas import CompanyEvent, EventMessage
from aider.integrations.discord import format_lifecycle_event_message


def test_forced_reviewer_approval_lifecycle_formats_as_warning():
    event = EventMessage(
        event=CompanyEvent.LIFECYCLE,
        task_id="task-1",
        payload={
            "name": "reviewer_forced_approval",
            "review_iteration": 3,
            "max_iterations": 3,
            "warning": (
                "Forced approval due to iteration limit - manual review strongly recommended"
            ),
            "severity": "warning",
        },
    )

    message = format_lifecycle_event_message(event)

    assert message.startswith("⚠️ **Reviewer Forced Approval**")
    assert "Warning: Forced approval due to iteration limit" in message
