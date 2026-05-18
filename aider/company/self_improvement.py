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
from aider.memory.evidence import SkillEvidenceCluster, collect_evidence_for_project
from aider.memory.store import MemoryStore


class SelfImprovementService:
    """Additive procedural-memory learner for Aider Plus company agents.

    This service intentionally runs after the existing audit/playbook learning.
    With default risk controls it creates pending skill proposals only; approved
    skill writes are handled separately by CompanySkillManager.approve_proposal().
    """

    def __init__(
        self, state: CompanyStateManager, config: SkillLearningConfig | None = None
    ):
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

        audit_proposals = self._audit_proposals(project, final_deliverable)
        memory_proposals = self.learn_from_memory(project, persist=False)
        return self._store_new_proposals([*audit_proposals, *memory_proposals])

    def learn_from_memory(
        self, project: Project, *, persist: bool = True
    ) -> list[SkillProposal]:
        """Generate higher-signal skill proposals from structured memory evidence."""

        if not self.config.enabled:
            return []
        store = MemoryStore(self.state.memory)
        clusters = collect_evidence_for_project(
            project, store, min_records=self.config.min_successful_repetitions
        )
        proposals = [
            proposal
            for cluster in clusters
            if (proposal := self._proposal_for_evidence_cluster(project, cluster))
            is not None
        ]
        if persist:
            return self._store_new_proposals(proposals)
        return proposals

    def _audit_proposals(
        self, project: Project, final_deliverable: Deliverable
    ) -> list[SkillProposal]:
        proposals: list[SkillProposal] = []
        audit = self.state.get_audit_log()
        by_department: dict[str, list[dict[str, Any]]] = {}
        for event in audit:
            department = str(
                event.get("department") or event.get("actor") or ""
            ).lower()
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
            proposal = self._proposal_for_department(
                project, final_deliverable, department, events
            )
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    def _proposal_for_evidence_cluster(
        self, project: Project, cluster: SkillEvidenceCluster
    ) -> SkillProposal | None:
        if not cluster.records or not cluster.procedure_steps:
            return None
        scope = cluster.suggested_scope
        title = f"{cluster.department.title()} {cluster.channel} evidence workflow for {project.name}"
        name = slugify_skill_name(title)
        numbered_steps = [
            f"{idx}. {step}"
            for idx, step in enumerate(cluster.procedure_steps, start=1)
        ]
        evidence_lines = []
        for record in cluster.records[:6]:
            content = str(record.content or "").replace("\n", " ")[:180]
            evidence_lines.append(f"- `{record.id}` ({record.kind}): {content}")
        content = "\n".join(
            [
                f"# {title}",
                (
                    "Description: Memory-backed procedural workflow learned from "
                    f"successful {cluster.department} evidence in {project.name}."
                ),
                "",
                "## When to use",
                (
                    f"Use this skill when a {cluster.department} task on `{project.name}` "
                    f"matches the {cluster.channel} pattern summarized by prior memory records."
                ),
                "",
                "## Procedure",
                *numbered_steps,
                "",
                "## Outcome summary",
                cluster.outcome_summary,
                "",
                "## Evidence from memory records",
                *(evidence_lines or ["- No compact evidence captured."]),
                "",
                "## Safety notes",
                "- This skill is a proposal until a human approves it.",
                "- Re-check current project instructions, approvals, and validation commands before applying it.",
                "- If memory evidence conflicts with current requirements, prefer current requirements.",
            ]
        )
        digest = hashlib.sha1(
            (project.project_id + cluster.cluster_id + name).encode("utf-8")
        ).hexdigest()[:12]
        return SkillProposal(
            proposal_id=f"skill-memory-{cluster.department}-{digest}",
            action="create",
            scope=scope,
            name=name,
            title=title,
            content=content,
            rationale=(
                "Structured memory records show a repeated successful procedure "
                "with source evidence and extractable steps."
            ),
            source_tasks=cluster.source_tasks,
            source_memory_records=cluster.source_memory_records,
            procedure_steps=cluster.procedure_steps,
            outcome_summary=cluster.outcome_summary,
            suggested_scope=scope,
            confidence=cluster.confidence,
            metadata={
                "project_id": project.project_id,
                "source": "memory",
                "cluster_id": cluster.cluster_id,
                "department": cluster.department,
                "channel": cluster.channel,
                "thread_id": cluster.thread_id,
                "outcome": cluster.outcome,
            },
        )

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
                event.get("event_type")
                or event.get("event")
                or event.get("type")
                or "event"
            )
            payload = str(event.get("payload_summary") or event.get("payload") or "")[
                :220
            ].replace("\n", " ")
            examples.append(f"- {event_type}: {payload}")
        task_list = ", ".join(source_tasks) or "unknown"
        evidence = (
            "\n".join(examples) if examples else "- No compact evidence captured."
        )
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
            metadata={
                "project_id": project.project_id,
                "final_task_id": final_deliverable.task_id,
            },
        )

    def _store_new_proposals(
        self, proposals: list[SkillProposal]
    ) -> list[SkillProposal]:
        stored: list[SkillProposal] = []
        seen: set[tuple[str, str]] = set()
        for proposal in proposals:
            key = (proposal.scope, proposal.name)
            if key in seen or self._proposal_exists(proposal):
                continue
            seen.add(key)
            if self.config.auto_create and not self.config.require_human_approval:
                self.skills.manager.create_skill(
                    scope=proposal.scope,
                    name=proposal.name,
                    content=proposal.content,
                    metadata=self._proposal_metadata(
                        proposal, approval_status="auto_created"
                    ),
                )
                proposal.status = "approved"
            else:
                self.skills.create_proposal(proposal)
            stored.append(proposal)
        return stored

    @staticmethod
    def _proposal_metadata(
        proposal: SkillProposal, *, approval_status: str
    ) -> dict[str, Any]:
        return {
            "approval_status": approval_status,
            "source_tasks": proposal.source_tasks,
            "source_audit_events": proposal.source_audit_events,
            "source_memory_records": proposal.source_memory_records,
            "procedure_steps": proposal.procedure_steps,
            "outcome_summary": proposal.outcome_summary,
            "suggested_scope": proposal.suggested_scope,
            "confidence": proposal.confidence,
            **proposal.metadata,
        }

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
        metadata = (
            event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        )
        status = str(metadata.get("status") or event.get("status") or "").lower()
        event_name = str(
            event.get("event_type") or event.get("event") or event.get("type") or ""
        ).lower()
        return (
            status == "success"
            or "success" in event_name
            or event_name in {"deliverable_produced", "task_submitted"}
        )
