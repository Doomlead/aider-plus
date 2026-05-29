"""Shared Company message formatting for chat, GUI, API, and tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aider.company.events import CompanyEvent as RuntimeCompanyEvent, EventBus
from aider.company.schemas import CompanyEvent, EventMessage

if TYPE_CHECKING:
    from aider.company.orchestrator import CompanyOrchestrator
    from aider.memory import ProjectMemory

SEVERITY_ICONS = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}
STATUS_ICONS = {
    "queued": "🕓",
    "pending": "🕓",
    "running": "🏃",
    "in_progress": "🏃",
    "success": "✅",
    "done": "✅",
    "completed": "✅",
    "complete": "✅",
    "passed": "✅",
    "failed": "❌",
    "failure": "❌",
    "error": "❌",
    "blocked": "🚧",
    "warning": "⚠️",
    "needs_review": "⚠️",
    "partial_success": "⚠️",
}
SEVERITY_COLORS = {
    "info": "blue",
    "success": "green",
    "warning": "yellow",
    "error": "red",
}
EVENT_TYPE_ICONS = {
    "lifecycle": "🔄",
    "daemon_run_progress": "🛰️",
    "coo_action_taken": "🤖",
    "deployment_completed": "🚀",
    "deployment_complete": "🚀",
    "approval_required": "📋",
    "approval_requested": "📋",
    "project_blocked": "🚧",
    "department_event": "🏢",
    "skill_proposal_updated": "🧠",
}
ANSI_COLORS = {
    "info": "\033[36m",
    "success": "\033[32m",
    "warning": "\033[33m",
    "error": "\033[31m",
}
ANSI_RESET = "\033[0m"


def _status_value(payload: dict) -> str:
    return str(payload.get("status") or "").strip().lower()


def status_label(status: object) -> str:
    """Return a consistent human label for status values across surfaces."""

    return str(status or "unknown").strip().replace("_", " ").title()


def format_status_badge(status: object) -> str:
    """Render a compact status badge shared by CLI, GUI, and chat adapters."""

    value = str(status or "unknown").strip().lower()
    icon = STATUS_ICONS.get(value, SEVERITY_ICONS["info"])
    return f"{icon} {status_label(value)}"


def _display_status(payload: dict) -> str:
    status = _status_value(payload)
    if status in {"success", "done", "completed", "complete", "passed"}:
        return "success"
    if status in {"failed", "failure", "error", "blocked"}:
        return "error"
    if status in {"warning", "needs_review", "partial_success"}:
        return "warning"
    return "info"


def event_icon(event: RuntimeCompanyEvent) -> str:
    status_severity = _display_status(event.payload or {})
    severity = status_severity if status_severity != "info" else event.severity
    status = _status_value(event.payload or {})
    if status in STATUS_ICONS:
        return STATUS_ICONS[status]
    if severity in {"success", "warning", "error"}:
        return SEVERITY_ICONS[severity]
    return EVENT_TYPE_ICONS.get(event.event_type, "🔄")


def event_title(event: RuntimeCompanyEvent) -> str:
    payload = event.payload or {}
    raw = payload.get("title") or payload.get("name") or event.event_type
    return str(raw).replace("_", " ").title()


def event_subject(event: RuntimeCompanyEvent) -> str:
    payload = event.payload or {}
    return str(
        payload.get("task_id")
        or payload.get("issue_id")
        or payload.get("project_name")
        or event.session_id
    )


def _detail_lines(event: RuntimeCompanyEvent) -> list[str]:
    payload = event.payload or {}
    metadata = payload.get("metadata") or {}
    department = metadata.get("department") if isinstance(metadata, dict) else None
    department = department or payload.get("department")
    details: list[str] = []
    if event.severity != "info":
        details.append(f"Severity: `{event.severity}`")
    for label, key in (
        ("Department", department),
        (
            "Status",
            format_status_badge(payload.get("status"))
            if payload.get("status")
            else None,
        ),
        ("Stage", payload.get("stage")),
        ("Environment", payload.get("environment")),
        ("Gate", payload.get("gate_name")),
    ):
        if key:
            if label == "Status":
                details.append(f"{label}: {key}")
            else:
                details.append(f"{label}: `{key}`")
    completed = payload.get("completed_count")
    total = payload.get("total_stages")
    if completed is not None and total is not None:
        details.append(
            f"Progress: {completed}/{total} {progress_bar(int(completed), int(total))}"
        )
    completed_stages = payload.get("completed_stages") or []
    failed_stages = payload.get("failed_stages") or []
    if completed_stages:
        details.append("Completed: " + ", ".join(map(str, completed_stages[-6:])))
    if failed_stages:
        details.append("Failed: " + ", ".join(map(str, failed_stages[-6:])))
    if payload.get("deploy_url") or payload.get("deployed_url"):
        details.append(
            f"URL: {payload.get('deploy_url') or payload.get('deployed_url')}"
        )
    if payload.get("error"):
        details.append(f"Error: {payload.get('error')}")
    return details


def progress_bar(completed: int, total: int, width: int = 10) -> str:
    total = max(1, total)
    complete_width = max(0, min(width, round(width * completed / total)))
    return "[" + "█" * complete_width + "░" * (width - complete_width) + "]"


def format_event_rich(
    event: RuntimeCompanyEvent, *, compact: bool = False, ansi: bool = False
) -> str:
    """Format a typed EventBus event with shared severity icons and details."""

    payload = event.payload or {}
    status_severity = _display_status(payload)
    color_key = status_severity if status_severity != "info" else event.severity
    heading = f"{event_icon(event)} **{event_title(event)}**"
    subject = event_subject(event)
    if compact:
        status = payload.get("status")
        stage = payload.get("stage")
        rendered_status = format_status_badge(status) if status else None
        suffix = " · ".join(str(part) for part in (stage, rendered_status) if part)
        message = f"{heading} — `{subject}`" + (f" ({suffix})" if suffix else "")
    else:
        message = "\n".join([heading, f"Task: `{subject}`", *_detail_lines(event)])
    if ansi:
        color = ANSI_COLORS.get(color_key, ANSI_COLORS["info"])
        return f"{color}{message}{ANSI_RESET}"
    return message


def format_lifecycle_event_rich(
    event: RuntimeCompanyEvent, *, compact: bool = False, ansi: bool = False
) -> str:
    return format_event_rich(event, compact=compact, ansi=ansi)


def format_daemon_progress(
    event: RuntimeCompanyEvent, *, compact: bool = False, ansi: bool = False
) -> str:
    payload = event.payload or {}
    if (
        payload.get("completed_count") is None
        and payload.get("completed_stages") is not None
    ):
        payload = dict(payload)
        payload["completed_count"] = len(payload.get("completed_stages") or [])
        event = RuntimeCompanyEvent.from_dict({**event.to_dict(), "payload": payload})
    return format_event_rich(event, compact=compact, ansi=ansi)


def format_deployment_event(
    event: RuntimeCompanyEvent, *, compact: bool = False, ansi: bool = False
) -> str:
    return format_event_rich(event, compact=compact, ansi=ansi)


def format_discord_event_block(event: RuntimeCompanyEvent) -> str:
    """Render high-priority EventBus events as Discord-friendly embed-like blocks."""

    body = format_runtime_event_message(event, compact=False)
    return f">>> {body}"


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


def format_runtime_event_message(
    event: RuntimeCompanyEvent, *, compact: bool = False, ansi: bool = False
) -> str:
    """Format a typed EventBus event for chat, GUI timelines, logs, or APIs."""

    if event.event_type == "daemon_run_progress":
        return format_daemon_progress(event, compact=compact, ansi=ansi)[:1900]
    if event.event_type.startswith("deployment"):
        return format_deployment_event(event, compact=compact, ansi=ansi)[:1900]
    if event.event_type == "lifecycle":
        return format_lifecycle_event_rich(event, compact=compact, ansi=ansi)[:1900]
    return format_event_rich(event, compact=compact, ansi=ansi)[:1900]


def event_stream_response(event_bus: EventBus, limit: int = 50) -> str:
    """Return a simple SSE payload for API/MCP adapters to expose later."""

    return "".join(event_bus.event_stream(limit=limit))


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
    last_deployment = status.get("session", {}).get("metadata", {}).get(
        "last_deployment"
    ) or status.get("last_deployment")
    if last_deployment:
        lines.extend(
            [
                "",
                "**Last deployment**",
                (
                    f"• {last_deployment.get('status', 'unknown')} to "
                    f"{last_deployment.get('environment', 'unknown')} "
                    f"({last_deployment.get('git_tag', 'untagged')})"
                ),
            ]
        )
    summary = status.get("last_deliverable_summary")
    if summary:
        lines.extend(["", "**Last deliverable**", str(summary)[:800]])
    return "\n".join(lines)[:1900]


__all__ = [
    "CompanyEvent",
    "EventMessage",
    "format_approval_required_message",
    "format_status_badge",
    "format_lifecycle_event_message",
    "format_lifecycle_event_rich",
    "format_daemon_progress",
    "format_deployment_event",
    "format_discord_event_block",
    "format_event_rich",
    "format_runtime_event_message",
    "event_stream_response",
    "format_audit_log_message",
    "format_company_status_message",
    "format_coo_status_message",
]
