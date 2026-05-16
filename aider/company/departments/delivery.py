from __future__ import annotations

from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.department import Department
from aider.company.schemas import (
    CompanyTask,
    Deliverable,
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
        plan = self._create_project_plan(task)
        plan = self._update_progress(plan, task)
        plan.risks = self._assess_risks(task, plan)
        self.current_plan = plan

        await self._emit_lifecycle_event(
            task.task_id,
            "delivery_plan_created",
            {
                "formatted": "Delivery created a coordinated project plan.",
                "status": plan.status,
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

        status = (
            "failure"
            if any(r.severity == "high" and r.blockers for r in plan.risks)
            else "success"
        )
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="delivery_plan",
            payload=plan.to_markdown(),
            status=status,
            metadata={
                "handoff_to": "devops" if status == "success" else "engineering",
                "blocking": status != "success",
                "project_plan": plan.to_dict(),
                "risk_count": len(plan.risks),
                "high_risk_count": sum(1 for r in plan.risks if r.severity == "high"),
                "milestones": [m.to_dict() for m in plan.milestones],
                "risks": [r.to_dict() for r in plan.risks],
                "context": dict(task.context),
            },
        )

    def _create_project_plan(self, task: CompanyTask) -> ProjectPlan:
        payload = task.payload if isinstance(task.payload, dict) else {}
        context = task.context if isinstance(task.context, dict) else {}
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
        milestones = [
            Milestone(
                name="Product and UX alignment",
                description="Confirm PRD, design, and acceptance criteria remain aligned.",
                owner="delivery",
                status=(
                    "complete"
                    if context.get("prd_summary") or context.get("design_spec_summary")
                    else "pending"
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
                status="complete" if payload.get("engineering_result") else "in_progress",
                dependencies=["engineering"],
                exit_criteria=["Engineering deliverable is present", "Changed files are known"],
            ),
            Milestone(
                name="QA verification complete",
                description="Confirm QA outcome and known residual checks before release.",
                owner="qa",
                status="complete" if payload.get("qa_report") else "pending",
                dependencies=["qa"],
                exit_criteria=["QA report is captured", "Release criteria are explicit"],
            ),
            Milestone(
                name="Release handoff prepared",
                description="Prepare DevOps handoff with scope, risks, and rollback notes.",
                owner="devops",
                status="ready" if payload.get("qa_report") else "pending",
                dependencies=["delivery", "devops"],
                exit_criteria=["Deployment owner has a release artifact", "Risks and blockers are visible"],
            ),
        ]
        timeline = Timeline(
            summary="Coordinate Product → UX → Engineering → QA → Delivery → DevOps handoff.",
            cadence="daily async check-in until release",
            milestones=milestones,
            assumptions=[
                "Engineering output and QA report are the source of truth for release readiness.",
                "DevOps receives this plan before deployment begins.",
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
                "DevOps owns deployment, environment readiness, and rollback execution.",
            ],
        )

    def _update_progress(self, plan: ProjectPlan, task: CompanyTask) -> ProjectPlan:
        payload = task.payload if isinstance(task.payload, dict) else {}
        qa_report = payload.get("qa_report") or payload.get("qa_metadata")
        engineering_result = payload.get("engineering_result")
        completed = sum(1 for milestone in plan.milestones if milestone.status in {"complete", "ready"})
        if qa_report and engineering_result:
            plan.status = "release_ready"
            plan.progress_summary = "Engineering and QA artifacts are available; release handoff is ready."
        elif engineering_result:
            plan.status = "at_risk"
            plan.progress_summary = "Engineering output exists but QA evidence is incomplete."
        else:
            plan.status = "planning"
            plan.progress_summary = "Delivery plan initialized; upstream artifacts are still being collected."
        if completed:
            plan.progress_summary += f" {completed}/{len(plan.milestones)} milestones are complete or ready."
        return plan

    def _assess_risks(self, task: CompanyTask, plan: ProjectPlan) -> list[RiskRegister]:
        payload = task.payload if isinstance(task.payload, dict) else {}
        context = task.context if isinstance(task.context, dict) else {}
        risks: list[RiskRegister] = []

        qa_metadata = payload.get("qa_metadata") if isinstance(payload.get("qa_metadata"), dict) else {}
        qa_report = payload.get("qa_report")
        if not qa_report:
            risks.append(
                RiskRegister(
                    risk_id="RISK-QA-EVIDENCE",
                    description="QA evidence is missing from the delivery handoff.",
                    severity="high",
                    probability="medium",
                    impact="high",
                    mitigation="Route back through QA or require manual verification before release.",
                    blockers=["qa_report"],
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

    @staticmethod
    def _prd_title(context: dict) -> str | None:
        prd = context.get("prd_structured")
        if isinstance(prd, dict) and prd.get("title"):
            return str(prd["title"])
        return None
