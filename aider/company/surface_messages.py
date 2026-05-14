"""Shared Company message formatting for chat, GUI, API, and tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aider.company.schemas import CompanyEvent, EventMessage

if TYPE_CHECKING:
    from aider.company.orchestrator import CompanyOrchestrator
    from aider.memory import ProjectMemory


def format_approval_required_message(event: EventMessage) -> str:
    payload = event.payload
    project_name = payload.get("project_name") or "unknown-project"
    gate_name = payload.get("gate_name", "prd_approval")
    gate_label = (
        gate_name.replace("prd", "PRD").replace("_", " ").title().replace("Prd", "PRD")
    )
    handoff_to = payload.get("handoff_to") or "engineering"
    handoff_label = str(handoff_to).replace("_", " ").title()
    if gate_name == "prd_approval":
        title = "📋 **Product Department Deliverable Ready**"
    elif gate_name == "clarification_approval":
        title = "❓ **Product Clarification Required**"
    elif gate_name == "coo_human_escalation":
        title = "🚨 **COO Human Escalation Required**"
    else:
        title = "🧪 **QA Release Approval Required**"
    preview = str(payload.get("artifact_preview", "")).strip()
    quoted_preview = "\n".join(
        f"> {line}" if line else ">" for line in preview.splitlines()
    )
    return (
        f"{title}\n"
        f"Project: `{project_name}`\n"
        f"Gate: {gate_label} → {handoff_label}\n\n"
        "**Preview:**\n"
        f"{quoted_preview}"
    )


def format_lifecycle_event_message(event: EventMessage) -> str:
    payload = event.payload or {}
    event_name = payload.get("name") or str(event.event)
    lifecycle_labels = {
        "product_revision_start": "Product is revising PRD based on feedback…",
        "product_prd_revised": "Product revised PRD",
    }
    label = lifecycle_labels.get(event_name, str(event_name).replace("_", " ").title())
    icon = "⚠️" if payload.get("severity") == "warning" else "🔄"
    iteration = payload.get("iteration")
    suffix = f" (iteration {iteration})" if iteration is not None else ""
    details = []
    formatted = payload.get("formatted")
    if formatted:
        details.append(str(formatted))
    warning = payload.get("warning")
    if warning:
        details.append("Warning: " + str(warning))
    files = payload.get("files") or []
    if files:
        details.append("Files: " + ", ".join(str(path) for path in files[:8]))
    feedback = payload.get("feedback") or {}
    if isinstance(feedback, dict) and feedback.get("summary"):
        details.append("Review: " + str(feedback.get("summary")))
    checks = payload.get("checks") or []
    if checks:
        passed = sum(1 for check in checks if check.get("status") == "passed")
        details.append(f"Checks: {passed}/{len(checks)} passed")
    body = "\n".join(details)
    if body:
        body = "\n" + body
    return f"{icon} **{label}**{suffix}\nTask: `{event.task_id}`{body}"


def format_audit_log_message(project_memory: "ProjectMemory", limit: int = 10) -> str:
    from aider.company.audit import AuditLogViewer

    viewer = AuditLogViewer.from_project_memory(project_memory)
    rendered = viewer.render_text(limit=limit)
    return f"🧾 **Recent Audit Events**\n```\n{rendered[:1800]}\n```"


def format_company_status_message(orchestrator: "CompanyOrchestrator") -> str:
    rendered = orchestrator.company_status()
    return f"🏢 **Company Dashboard**\n```\n{rendered[:1800]}\n```"


def format_coo_status_message(status: dict) -> str:
    if not status:
        return "🤖 **CEO/COO Briefing**\nNo COO session is active yet."
    current_route = status.get("current_route") or {}
    last_action = status.get("last_coo_action") or {}
    lines = [
        "🤖 **CEO/COO Briefing**",
        f"Session: `{status.get('session_id')}`",
        f"Status: `{status.get('status')}`",
        f"COO action: `{last_action.get('action', '—')}`",
        f"Active department: `{status.get('active_department') or '—'}`",
        (
            "Current route: "
            f"`{current_route.get('strategy', '—')} → "
            f"{current_route.get('target', '—')}`"
        ),
    ]
    error_count = int(status.get("error_count", 0) or 0)
    if error_count > 0:
        last_error = status.get("last_error") or {}
        lines.extend(
            [
                "",
                "🚨 **Recent COO errors**",
                f"• Count: `{error_count}`",
                (
                    "• Last: "
                    f"`{last_error.get('error_type', 'unknown_error')}` "
                    f"after `{last_error.get('retries', 0)}` retries — "
                    f"{last_error.get('message', '')}"
                ),
                f"• Recovery: {last_error.get('recovery_suggestion', 'review COO activity')}",
            ]
        )
        if last_error.get("escalate_to_human"):
            lines.append(
                "• Human escalation pending"
                f" — approval `{last_error.get('approval_task_id', 'pending')}`"
            )
    lines.extend(["", "**Recent activity**"])
    events = status.get("recent_events") or []
    if events:
        lines.extend(f"• {event}" for event in events[-10:])
    else:
        lines.append("• No COO bus events yet.")
    summary = status.get("last_deliverable_summary")
    if summary:
        lines.extend(["", "**Last deliverable**", str(summary)[:800]])
    return "\n".join(lines)[:1900]


__all__ = [
    "CompanyEvent",
    "EventMessage",
    "format_approval_required_message",
    "format_lifecycle_event_message",
    "format_audit_log_message",
    "format_company_status_message",
    "format_coo_status_message",
]
