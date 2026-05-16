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
            "project.name",
            "project.phase",
            "project.prd",
        ]

    async def process(self, task: CompanyTask) -> Deliverable:
        plan = self._run_delivery_cycle(task)
        self.current_plan = plan
        handover = self.handover_to_devops(plan, task)

        await self._emit_lifecycle_event(
            task.task_id,
            "delivery_plan_created",
            {
                "formatted": "Delivery updated the authoritative project plan.",
                "status": plan.overall_status,
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
                    "delivery_blocker",
                    {
                        "formatted": f"Delivery blocker: {risk.description}",
                        "risk": risk.to_dict(),
                        "severity": "warning",
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

    def _run_delivery_cycle(self, task: CompanyTask) -> ProjectPlan:
        """Create or refresh a proactive plan for the current project phase."""
        plan = self._create_project_plan(task)
        plan.risks = self._assess_risks(task, plan)
        self._assess_project_health(plan, task)
        return plan

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
                description="Prepare DevOps handoff with scope, risks, and rollback notes.",
                owner="devops",
                status="ready" if has_qa and has_engineering else "pending",
                dependencies=["delivery", "devops"],
                exit_criteria=[
                    "Deployment owner has a release artifact",
                    "Risks and blockers are visible",
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
            ],
        )
        return ProjectPlan(
            title=title,
            objective=objective,
            milestones=milestones,
            timeline=timeline,
            dependencies=["product", "ux", "engineering", "qa", "devops"],
            cross_department_alignment=[
                "Product owns scope and acceptance criteria.",
                "UX owns experience constraints and accessibility details.",
                "Engineering owns implementation and technical tradeoffs.",
                "QA owns verification evidence and residual quality risks.",
                "Delivery owns timeline, blockers, release readiness, and DevOps handoff.",
                "DevOps owns deployment, environment readiness, and rollback execution.",
            ],
        )

    def _assess_project_health(
        self, plan: ProjectPlan, task: CompanyTask
    ) -> ProjectPlan:
        plan.completion_percentage = self._calculate_completion(plan)
        plan.critical_blockers = [
            blocker
            for risk in plan.risks
            if risk.status != "closed"
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
        if plan.critical_blockers:
            plan.status = "blocked"
            plan.overall_status = "blocked"
            plan.progress_summary = (
                "Critical blockers must be resolved before DevOps handoff."
            )
        elif has_engineering and has_qa and plan.completion_percentage >= 100:
            plan.status = "release_ready"
            plan.overall_status = "release_ready"
            plan.progress_summary = (
                "Engineering and QA artifacts are available; DevOps handoff is ready."
            )
        elif has_engineering:
            plan.status = "at_risk" if not has_qa else "on_track"
            plan.overall_status = plan.status
            plan.progress_summary = "Engineering output exists; Delivery is tracking QA and release readiness."
        else:
            plan.status = "planning"
            plan.overall_status = "planning"
            plan.progress_summary = "Delivery plan initialized proactively; upstream artifacts are still being collected."
        plan.progress_summary += (
            f" {plan.completion_percentage}% of milestones are complete or ready."
        )
        return plan

    def _calculate_completion(self, plan: ProjectPlan) -> int:
        if not plan.milestones:
            return 0
        weights = {"complete": 1.0, "ready": 1.0, "in_progress": 0.5}
        score = sum(weights.get(m.status, 0.0) for m in plan.milestones)
        return int(round((score / len(plan.milestones)) * 100))

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
            plan.completion_percentage >= 100
            and not plan.critical_blockers
            and bool(payload.get("engineering_result"))
            and bool(payload.get("qa_report") or payload.get("qa_metadata"))
        )
        return DeliveryHandover(
            project_name=str(
                context.get("project_name") or payload.get("project_name") or plan.title
            ),
            ready_for_devops=ready,
            delivery_summary=plan.to_summary(),
            release_scope=str(
                payload.get("prd_content")
                or context.get("prd_summary")
                or plan.objective
            ),
            critical_blockers=list(plan.critical_blockers),
            rollback_notes=[
                "Confirm deployment target and rollback owner before release.",
                "Use Engineering metadata and QA report as the validation baseline.",
            ],
            environment=str(context.get("environment", "production")),
        )

    @staticmethod
    def _prd_title(context: dict) -> str | None:
        prd = context.get("prd_structured")
        if isinstance(prd, dict) and prd.get("title"):
            return str(prd["title"])
        return None
