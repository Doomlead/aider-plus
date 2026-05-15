from __future__ import annotations

import hashlib
from typing import Any

from aider.company.project import Project
from aider.company.schemas import Deliverable
from aider.company.skills import (
    CompanySkillManager,
    SkillLearningConfig,
    SkillProposal,
    slugify_skill_name,
)
from aider.company.state import CompanyStateManager


class SelfImprovementService:
    """Additive procedural-memory learner for Aider Plus company agents.

    This service intentionally runs after the existing audit/playbook learning.
    With default risk controls it creates pending skill proposals only; approved
    skill writes are handled separately by CompanySkillManager.approve_proposal().
    """

    def __init__(self, state: CompanyStateManager, config: SkillLearningConfig | None = None):
        self.state = state
        self.config = config or SkillLearningConfig()
        self.skills = CompanySkillManager(state, self.config)

    def learn_from_post_mortem(
        self,
        project: Project,
        final_deliverable: Deliverable,
    ) -> list[SkillProposal]:
        if not self.config.enabled:
            return []
        proposals: list[SkillProposal] = []
        audit = self.state.get_audit_log()
        by_department: dict[str, list[dict[str, Any]]] = {}
        for event in audit:
            department = str(event.get("department") or event.get("actor") or "").lower()
            if department:
                by_department.setdefault(department, []).append(event)

        for department, events in by_department.items():
            if department not in {
                "product",
                "ux",
                "engineering",
                "reviewer",
                "qa",
                "devops",
                "coo",
            }:
                continue
            success_count = sum(1 for event in events if self._event_is_success(event))
            if success_count < self.config.min_successful_repetitions:
                continue
            proposal = self._proposal_for_department(project, final_deliverable, department, events)
            if proposal is None or self._proposal_exists(proposal):
                continue
            if self.config.auto_create and not self.config.require_human_approval:
                self.skills.manager.create_skill(
                    scope=proposal.scope,
                    name=proposal.name,
                    content=proposal.content,
                    metadata={
                        "approval_status": "auto_created",
                        "source_tasks": proposal.source_tasks,
                        "source_audit_events": proposal.source_audit_events,
                        "confidence": proposal.confidence,
                    },
                )
                proposal.status = "approved"
            else:
                self.skills.create_proposal(proposal)
            proposals.append(proposal)
        return proposals

    def _proposal_for_department(
        self,
        project: Project,
        final_deliverable: Deliverable,
        department: str,
        events: list[dict[str, Any]],
    ) -> SkillProposal | None:
        title = f"{department.title()} workflow for {project.name}"
        name = slugify_skill_name(title)
        source_tasks = sorted(
            {
                str(e.get("metadata", {}).get("task_id") or e.get("task_id") or "")
                for e in events
                if (e.get("metadata", {}).get("task_id") or e.get("task_id"))
            }
        )[:10]
        event_ids = [
            str(e.get("event_id") or e.get("id") or e.get("timestamp") or idx)
            for idx, e in enumerate(events[:10])
        ]
        examples = []
        for event in events[-5:]:
            event_type = (
                event.get("event_type") or event.get("event") or event.get("type") or "event"
            )
            payload = str(
                event.get("payload_summary") or event.get("payload") or ""
            )[:220].replace("\n", " ")
            examples.append(f"- {event_type}: {payload}")
        task_list = ", ".join(source_tasks) or "unknown"
        evidence = "\n".join(examples) if examples else "- No compact evidence captured."
        content = "\n".join(
            [
                f"# {title}",
                (
                    "Description: Learned procedural workflow for recurring "
                    f"{department} tasks in {project.name}."
                ),
                "",
                "## When to use",
                (
                    f"Use this skill when a {department} task resembles prior "
                    f"successful work for project `{project.name}` or task ids: {task_list}."
                ),
                "",
                "## Procedure",
                (
                    "1. Review the current task request, project memory, relevant "
                    "playbook guidance, and any loaded design/PRD context."
                ),
                (
                    "2. Compare the request against the evidence below and reuse only "
                    "steps that match the current task."
                ),
                (
                    f"3. Preserve Aider Plus role boundaries: do {department} work in "
                    "this role and delegate other responsibilities through the "
                    "COO/orchestrator."
                ),
                (
                    "4. Prefer existing tests, validation commands, and approval gates "
                    "before treating the workflow as complete."
                ),
                (
                    "5. If this skill is wrong or stale, propose a patch instead of "
                    "silently following it."
                ),
                "",
                "## Evidence from prior runs",
                evidence,
                "",
                "## Safety notes",
                "- This skill is procedural guidance, not a hard rule.",
                "- System, developer, user, and project instructions take precedence.",
                "- Do not bypass approval gates or tool permissions.",
            ]
        )

        digest = hashlib.sha1(
            (department + project.project_id + name).encode("utf-8")
        ).hexdigest()[:12]
        return SkillProposal(
            proposal_id=f"skill-{department}-{digest}",
            action="create",
            scope=department,
            name=name,
            title=title,
            content=content,
            rationale=(
                "Repeated successful department activity produced enough evidence "
                "for a reusable procedural workflow."
            ),
            source_tasks=source_tasks,
            source_audit_events=event_ids,
            confidence=min(0.95, 0.45 + 0.1 * len(source_tasks or events)),
            metadata={"project_id": project.project_id, "final_task_id": final_deliverable.task_id},
        )

    def _proposal_exists(self, proposal: SkillProposal) -> bool:
        try:
            if self.skills.manager.read_skill(proposal.scope, proposal.name):
                return True
        except FileNotFoundError:
            pass
        return any(
            existing.scope == proposal.scope
            and existing.name == proposal.name
            and existing.status == "pending"
            for existing in self.skills.list_proposals(status="pending")
        )

    @staticmethod
    def _event_is_success(event: dict[str, Any]) -> bool:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        status = str(metadata.get("status") or event.get("status") or "").lower()
        event_name = str(
            event.get("event_type") or event.get("event") or event.get("type") or ""
        ).lower()
        return (
            status == "success"
            or "success" in event_name
            or event_name in {"deliverable_produced", "task_submitted"}
        )
