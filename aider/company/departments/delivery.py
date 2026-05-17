from __future__ import annotations

from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.department import Department
from aider.company.schemas import (
    CompanyTask,
    Deliverable,
    DeliveryHandover,
    Milestone,
    ProjectPlan,
    RiskRegister,
    Timeline,
)
from aider.memory import ConversationMemory, ProjectMemory


class DeliveryDepartment(Department):
    """Delivery / Project Management coordinator for milestones, risks, and timeline."""

    name = "delivery"
    allowed_tools: list[str] = []

    def __init__(
        self,
        project_memory: ProjectMemory,
        agent_loop: Optional[AiderAgentLoop] = None,
        conversation_memory: Optional[ConversationMemory] = None,
        config: Optional[DepartmentConfig] = None,
    ):
        super().__init__(project_memory, conversation_memory, config=config)
        self.agent_loop = agent_loop
        self.current_plan: ProjectPlan | None = None

    def get_context_requirements(self) -> list[str]:
        return [
            "playbook.*",
            "skills.shared",
            "skills.delivery",
            "skills.project_management",
            "project.name",
            "project.phase",
            "project.prd",
        ]

    async def process(self, task: CompanyTask) -> Deliverable:
        """Refresh Delivery's authoritative state and produce release handoff data."""
        plan = self._generate_project_plan(task)
        self.current_plan = plan
        handover = self.handover_to_devops(plan, task)

        await self._emit_lifecycle_event(
            task.task_id,
            "delivery_plan_updated",
            {
                "formatted": "Delivery updated the authoritative project plan.",
                "status": plan.status,
                "completion_percentage": plan.completion_percentage,
                "weighted_completion": plan.weighted_completion,
                "next_milestone": plan.next_milestone,
                "critical_blockers": list(plan.critical_blockers),
                "executive_summary": plan.executive_summary,
            },
        )
        # Compatibility for existing event consumers while the richer event rolls out.
        await self._emit_lifecycle_event(
            task.task_id,
            "delivery_plan_created",
            {
                "formatted": "Delivery updated the authoritative project plan.",
                "status": plan.status,
                "completion_percentage": plan.completion_percentage,
                "next_milestone": plan.next_milestone,
                "milestone_count": len(plan.milestones),
                "risk_count": len(plan.risks),
            },
        )
        for milestone in plan.milestones:
            await self._emit_lifecycle_event(
                task.task_id,
                "milestone_updated",
                {
                    "formatted": f"Delivery updated milestone: {milestone.name}",
                    "milestone": milestone.to_dict(),
                },
            )
        for risk in plan.risks:
            await self._emit_lifecycle_event(
                task.task_id,
                "risk_identified",
                {
                    "formatted": f"Delivery identified risk: {risk.description}",
                    "risk": risk.to_dict(),
                    "severity": risk.severity,
                },
            )
            if risk.severity == "high" or risk.blockers:
                await self._emit_lifecycle_event(
                    task.task_id,
                    "project_at_risk",
                    {
                        "formatted": f"Delivery marked project at risk: {risk.description}",
                        "risk": risk.to_dict(),
                        "severity": "warning",
                    },
                )
                await self._emit_lifecycle_event(
                    task.task_id,
                    "delivery_blocker",
                    {
                        "formatted": f"Delivery blocker: {risk.description}",
                        "risk": risk.to_dict(),
                        "severity": "warning",
                    },
                )
            elif risk.status in {"closed", "resolved"}:
                await self._emit_lifecycle_event(
                    task.task_id,
                    "delivery_blocker_resolved",
                    {
                        "formatted": f"Delivery blocker resolved: {risk.description}",
                        "risk": risk.to_dict(),
                    },
                )
        if handover.ready_for_devops:
            await self._emit_lifecycle_event(
                task.task_id,
                "ready_for_release",
                {
                    "formatted": "Delivery readiness gate is green; DevOps handoff is ready.",
                    "handover": handover.to_dict(),
                },
            )

        blocking = bool(plan.critical_blockers)
        status = "failure" if blocking else "success"
        metadata = {
            "blocking": blocking,
            "project_plan": plan.to_dict(),
            "delivery_summary": plan.to_summary(),
            "delivery_handover": handover.to_dict(),
            "ready_for_devops": handover.ready_for_devops,
            "risk_count": len(plan.risks),
            "high_risk_count": sum(1 for r in plan.risks if r.severity == "high"),
            "critical_blockers": list(plan.critical_blockers),
            "milestones": [m.to_dict() for m in plan.milestones],
            "risks": [r.to_dict() for r in plan.risks],
            "context": dict(task.context),
        }
        if handover.ready_for_devops:
            metadata["handoff_to"] = "devops"
        elif blocking:
            metadata["handoff_to"] = "engineering"

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="delivery_plan",
            payload=plan.to_markdown(),
            status=status,
            metadata=metadata,
        )

    def _generate_project_plan(self, task: CompanyTask) -> ProjectPlan:
        """Create the first authoritative Delivery plan for current project state."""
        plan = self._create_project_plan(task)
        return self._monitor_and_update(plan, task)

    def _monitor_and_update(self, plan: ProjectPlan, task: CompanyTask) -> ProjectPlan:
        """Proactively recompute risks, blockers, milestones, and executive status."""
        plan.risks = self._assess_risks(task, plan)
        self._assess_project_health(plan, task)
        return plan

    # Backward-compatible alias for existing tests/extensions.
    def _run_delivery_cycle(self, task: CompanyTask) -> ProjectPlan:
        return self._generate_project_plan(task)

    def _create_project_plan(self, task: CompanyTask) -> ProjectPlan:
        payload = task.payload if isinstance(task.payload, dict) else {}
        context = task.context if isinstance(task.context, dict) else {}
        phase = str(
            context.get("project_phase")
            or context.get("current_project_phase")
            or "planning"
        )
        title = str(
            context.get("project_name")
            or payload.get("project_name")
            or self._prd_title(context)
            or "Delivery Plan"
        )
        objective = str(
            payload.get("prd_content")
            or context.get("prd_summary")
            or context.get("original_request")
            or "Coordinate the approved work through release."
        )[:800]
        has_prd = bool(context.get("prd_summary") or payload.get("prd_content"))
        has_design = bool(
            context.get("design_spec_summary") or payload.get("design_spec")
        )
        has_engineering = bool(
            payload.get("engineering_result") or context.get("engineering_result")
        )
        has_qa = bool(payload.get("qa_report") or payload.get("qa_metadata"))
        skill_guidance = self._format_guidance(context.get("skill_guidance"))
        playbook_guidance = self._format_guidance(context.get("playbook_guidance"))
        milestones = [
            Milestone(
                name="Product and UX alignment",
                description="Confirm PRD, design, and acceptance criteria remain aligned.",
                owner="delivery",
                status=(
                    "complete"
                    if has_prd and (has_design or phase != "design")
                    else "in_progress" if has_prd else "pending"
                ),
                dependencies=["product", "ux"],
                exit_criteria=[
                    "Scope is understood",
                    "Design and acceptance criteria are linked",
                ],
            ),
            Milestone(
                name="Engineering implementation ready",
                description="Confirm implementation is available and ready for verification.",
                owner="engineering",
                status=(
                    "complete"
                    if has_engineering
                    else "in_progress" if phase == "development" else "pending"
                ),
                dependencies=["engineering"],
                exit_criteria=[
                    "Engineering deliverable is present",
                    "Changed files are known",
                ],
            ),
            Milestone(
                name="QA verification complete",
                description="Confirm QA outcome and known residual checks before release.",
                owner="qa",
                status=(
                    "complete"
                    if has_qa
                    else "in_progress" if phase == "qa" else "pending"
                ),
                dependencies=["qa"],
                exit_criteria=[
                    "QA report is captured",
                    "Release criteria are explicit",
                ],
            ),
            Milestone(
                name="Release handoff prepared",
                description="Prepare DevOps handoff with scope, risks, release notes, and rollback plan.",
                owner="delivery",
                status="complete" if has_qa and has_engineering else "pending",
                dependencies=["delivery", "devops"],
                exit_criteria=[
                    "Deployment owner has a release artifact",
                    "Risks and blockers are visible",
                    "Go/no-go recommendation is explicit",
                ],
            ),
        ]
        timeline = Timeline(
            summary="Coordinate Product → UX → Engineering → QA → Delivery → DevOps handoff.",
            cadence="daily async check-in until release",
            milestones=milestones,
            assumptions=[
                "Delivery owns timeline, milestone, blocker, and release-readiness visibility.",
                "DevOps receives an explicit handover before deployment begins.",
                "Skills and playbook guidance inform Delivery review when available.",
            ],
        )
        key_dependencies = [
            "QA verification evidence",
            "Engineering deliverable",
            "DevOps environment readiness",
        ]
        if skill_guidance:
            key_dependencies.append(
                "Applicable delivery/project-management skill guidance"
            )
        if playbook_guidance:
            key_dependencies.append("Relevant playbook guidance")
        return ProjectPlan(
            title=title,
            objective=objective,
            milestones=milestones,
            timeline=timeline,
            dependencies=["product", "ux", "engineering", "qa", "delivery", "devops"],
            key_dependencies=key_dependencies,
            cross_department_alignment=[
                "Product owns scope and acceptance criteria.",
                "UX owns experience constraints and accessibility details.",
                "Engineering owns implementation and technical tradeoffs.",
                "QA owns verification evidence and residual quality risks.",
                "Delivery owns project state, blockers, completion, release readiness, and DevOps handoff.",
                "DevOps owns deployment, environment readiness, and rollback execution.",
                *(f"Skill guidance: {item}" for item in skill_guidance[:3]),
                *(f"Playbook guidance: {item}" for item in playbook_guidance[:3]),
            ],
        )

    def _assess_project_health(
        self, plan: ProjectPlan, task: CompanyTask
    ) -> ProjectPlan:
        plan.completion_percentage = self._calculate_completion(plan)
        plan.weighted_completion = self._calculate_weighted_completion(plan)
        plan.critical_blockers = [
            blocker
            for risk in plan.risks
            if risk.status not in {"closed", "resolved"}
            for blocker in risk.blockers
        ]
        next_milestone = next(
            (m.name for m in plan.milestones if m.status not in {"complete", "ready"}),
            None,
        )
        plan.next_milestone = next_milestone or "Release handoff prepared"
        payload = task.payload if isinstance(task.payload, dict) else {}
        has_engineering = bool(payload.get("engineering_result"))
        has_qa = bool(payload.get("qa_report") or payload.get("qa_metadata"))
        high_open_risks = [
            r
            for r in plan.risks
            if r.severity == "high" and r.status not in {"closed", "resolved"}
        ]
        if plan.critical_blockers:
            plan.status = "delayed"
            plan.overall_status = "delayed"
            plan.progress_summary = (
                "Critical blockers must be resolved before DevOps handoff."
            )
        elif has_engineering and has_qa and plan.weighted_completion >= 100:
            plan.status = "complete"
            plan.overall_status = "complete"
            plan.progress_summary = "Engineering and QA artifacts are available; Delivery has prepared release handoff."
        elif high_open_risks or (has_engineering and not has_qa):
            plan.status = "at_risk"
            plan.overall_status = "at_risk"
            plan.progress_summary = "Delivery is tracking unresolved release-readiness risk before DevOps handoff."
        else:
            plan.status = "on_track"
            plan.overall_status = "on_track"
            plan.progress_summary = "Delivery is proactively coordinating upstream artifacts and release readiness."
        plan.progress_summary += f" Weighted completion is {plan.weighted_completion}%."
        plan.executive_summary = self._executive_summary(plan)
        return plan

    def _calculate_completion(self, plan: ProjectPlan) -> int:
        if not plan.milestones:
            return 0
        weights = {"complete": 1.0, "ready": 1.0, "in_progress": 0.5}
        score = sum(weights.get(m.status, 0.0) for m in plan.milestones)
        return int(round((score / len(plan.milestones)) * 100))

    def _calculate_weighted_completion(self, plan: ProjectPlan) -> int:
        milestone_weights = {
            "Product and UX alignment": 20,
            "Engineering implementation ready": 35,
            "QA verification complete": 30,
            "Release handoff prepared": 15,
        }
        total = sum(milestone_weights.get(m.name, 10) for m in plan.milestones)
        if not total:
            return 0
        status_weight = {"complete": 1.0, "ready": 1.0, "in_progress": 0.5}
        score = sum(
            milestone_weights.get(m.name, 10) * status_weight.get(m.status, 0.0)
            for m in plan.milestones
        )
        return int(round((score / total) * 100))

    def _assess_risks(self, task: CompanyTask, plan: ProjectPlan) -> list[RiskRegister]:
        payload = task.payload if isinstance(task.payload, dict) else {}
        context = task.context if isinstance(task.context, dict) else {}
        phase = str(
            context.get("project_phase") or context.get("current_project_phase") or ""
        )
        risks: list[RiskRegister] = []

        qa_metadata = (
            payload.get("qa_metadata")
            if isinstance(payload.get("qa_metadata"), dict)
            else {}
        )
        qa_report = payload.get("qa_report")
        engineering_result = payload.get("engineering_result")
        if not qa_report:
            release_phase = phase in {
                "qa",
                "delivery",
                "release_ready",
                "deploying",
            } or bool(engineering_result)
            risks.append(
                RiskRegister(
                    risk_id="RISK-QA-EVIDENCE",
                    description="QA evidence is missing from the delivery timeline.",
                    severity="high" if release_phase else "medium",
                    probability="medium",
                    impact="high",
                    mitigation="Route through QA or require manual verification before release.",
                    blockers=["qa_report"] if release_phase else [],
                )
            )
        if qa_metadata.get("test_coverage") == "manual_required":
            risks.append(
                RiskRegister(
                    risk_id="RISK-MANUAL-QA",
                    description="QA did not execute automated tests and manual verification is required.",
                    severity="medium",
                    probability="medium",
                    impact="medium",
                    mitigation="Capture manual verification notes or add targeted tests before deployment.",
                )
            )
        if context.get("design_spec_validation_errors"):
            risks.append(
                RiskRegister(
                    risk_id="RISK-DESIGN-SCHEMA",
                    description="Design specification validation warnings may affect implementation fidelity.",
                    severity="medium",
                    probability="low",
                    impact="medium",
                    mitigation="Confirm UX acceptance criteria before release approval.",
                )
            )
        if not risks:
            risks.append(
                RiskRegister(
                    risk_id="RISK-RELEASE-COORDINATION",
                    description="Release requires continued cross-department alignment through deployment.",
                    severity="low",
                    probability="medium",
                    impact="medium",
                    mitigation="Use Delivery plan milestones as the release checklist.",
                    status="monitoring",
                )
            )
        return risks

    def handover_to_devops(
        self, plan: ProjectPlan, task: CompanyTask
    ) -> DeliveryHandover:
        payload = task.payload if isinstance(task.payload, dict) else {}
        context = task.context if isinstance(task.context, dict) else {}
        ready = (
            plan.status == "complete"
            and plan.weighted_completion >= 100
            and not plan.critical_blockers
            and bool(payload.get("engineering_result"))
            and bool(payload.get("qa_report") or payload.get("qa_metadata"))
        )
        release_scope = str(
            payload.get("prd_content") or context.get("prd_summary") or plan.objective
        )
        blockers = list(plan.critical_blockers)
        return DeliveryHandover(
            project_name=str(
                context.get("project_name") or payload.get("project_name") or plan.title
            ),
            ready_for_devops=ready,
            delivery_summary=plan.to_summary(),
            release_scope=release_scope,
            critical_blockers=blockers,
            rollback_notes=[
                "Confirm deployment target and rollback owner before release.",
                "Use Engineering metadata and QA report as the validation baseline.",
            ],
            go_no_go_recommendation=(
                "GO: Delivery confirms scope, QA evidence, blockers, release notes, and rollback plan are ready."
                if ready
                else f"NO-GO: resolve {', '.join(blockers) if blockers else 'remaining release-readiness gaps'} before DevOps."
            ),
            release_notes_draft=self._release_notes_draft(plan, payload, release_scope),
            rollback_plan=self._rollback_plan(plan, payload),
            environment=str(context.get("environment", "production")),
            deployment_target=(
                payload.get("deployment_target") or context.get("deployment_target")
            ),
        )

    def _executive_summary(self, plan: ProjectPlan) -> str:
        blockers = (
            ", ".join(plan.critical_blockers) if plan.critical_blockers else "none"
        )
        return (
            f"Delivery status is {plan.status} at {plan.weighted_completion}% weighted completion. "
            f"Next milestone: {plan.next_milestone or 'TBD'}. Critical blockers: {blockers}."
        )

    def _release_notes_draft(
        self, plan: ProjectPlan, payload: dict, release_scope: str
    ) -> str:
        engineering_metadata = (
            payload.get("engineering_metadata") if isinstance(payload, dict) else {}
        )
        files = []
        if isinstance(engineering_metadata, dict):
            files = list(
                engineering_metadata.get("files")
                or engineering_metadata.get("changed_files")
                or []
            )
        file_note = (
            f"\n\nChanged files: {', '.join(map(str, files[:10]))}." if files else ""
        )
        return f"Release scope: {release_scope[:500]}\n\nDelivery summary: {plan.executive_summary}{file_note}"

    def _rollback_plan(self, plan: ProjectPlan, payload: dict) -> str:
        return (
            "1. Pause rollout and notify Delivery, Engineering, QA, and DevOps owners.\n"
            "2. Revert to the previous known-good artifact or commit identified by DevOps.\n"
            "3. Run the QA validation baseline from the Delivery handover.\n"
            "4. Reopen Delivery blockers/risks and keep the project in delayed status until validated."
        )

    @staticmethod
    def _format_guidance(guidance) -> list[str]:
        if not guidance:
            return []
        if isinstance(guidance, list):
            return [str(item) for item in guidance if item]
        return [str(guidance)]

    @staticmethod
    def _prd_title(context: dict) -> str | None:
        prd = context.get("prd_structured")
        if isinstance(prd, dict) and prd.get("title"):
            return str(prd["title"])
        return None
