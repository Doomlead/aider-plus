from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from aider.company.interfaces import Deliverable
from aider.company.project import Project
from aider.company.schemas import CompanyTask


@dataclass(frozen=True)
class LifecycleTransition:
    """A valid phase transition in the company lifecycle state table."""

    from_phase: str
    event: str
    to_phase: str
    department: Optional[str] = None
    status: Optional[str] = None
    description: str = ""


TransitionKey = Tuple[str, str, Optional[str], Optional[str]]


class LifecycleEngine:
    """Central owner for company project phase transitions."""

    TRANSITIONS: Dict[TransitionKey, LifecycleTransition] = {
        ("prototyping", "prd_approved", "product", "success"): LifecycleTransition(
            "prototyping",
            "prd_approved",
            "development",
            department="product",
            status="success",
            description="CEO-approved PRD can move directly to Engineering.",
        ),
        (
            "prototyping",
            "prd_approved_for_design",
            "product",
            "success",
        ): LifecycleTransition(
            "prototyping",
            "prd_approved_for_design",
            "design",
            department="product",
            status="success",
            description="CEO-approved PRD requires UX before Engineering.",
        ),
        ("prototyping", "approval_rejected", "product", None): LifecycleTransition(
            "prototyping",
            "approval_rejected",
            "prototyping",
            department="product",
            description="Rejected PRD returns to Product for another prototype pass.",
        ),
        ("design", "deliverable_success", "ux", "success"): LifecycleTransition(
            "design",
            "deliverable_success",
            "development",
            department="ux",
            status="success",
            description="UX design approval moves work into Engineering.",
        ),
        (
            "development",
            "deliverable_success",
            "engineering",
            "success",
        ): LifecycleTransition(
            "development",
            "deliverable_success",
            "qa",
            department="engineering",
            status="success",
            description="Successful Engineering output moves to QA.",
        ),
        (
            "development",
            "deliverable_failure",
            "engineering",
            "failure",
        ): LifecycleTransition(
            "development",
            "deliverable_failure",
            "development",
            department="engineering",
            status="failure",
            description="Engineering failure stays in development for revision.",
        ),
        ("qa", "deliverable_done", "qa", None): LifecycleTransition(
            "qa",
            "deliverable_done",
            "delivery",
            department="qa",
            description="QA report moves to Delivery coordination before release approval.",
        ),
        ("delivery", "deliverable_done", "delivery", None): LifecycleTransition(
            "delivery",
            "deliverable_done",
            "release_ready",
            department="delivery",
            description="Delivery plan creates a release approval gate.",
        ),
        ("release_ready", "approval_approved", "delivery", None): LifecycleTransition(
            "release_ready",
            "approval_approved",
            "deploying",
            department="delivery",
            description="Delivery-approved release starts deployment.",
        ),
        ("release_ready", "approval_rejected", "delivery", None): LifecycleTransition(
            "release_ready",
            "approval_rejected",
            "development",
            department="delivery",
            description="Delivery release rejection routes back to Engineering.",
        ),
        ("release_ready", "approval_approved", "qa", None): LifecycleTransition(
            "release_ready",
            "approval_approved",
            "deploying",
            department="qa",
            description="Release approval starts deployment.",
        ),
        ("release_ready", "approval_rejected", "qa", None): LifecycleTransition(
            "release_ready",
            "approval_rejected",
            "development",
            department="qa",
            description="Release rejection routes back to Engineering.",
        ),
        ("deploying", "deliverable_done", "devops", None): LifecycleTransition(
            "deploying",
            "deliverable_done",
            "post_mortem",
            department="devops",
            description="Deployment always enters post-mortem before final routing.",
        ),
        (
            "post_mortem",
            "post_mortem_success",
            "devops",
            "success",
        ): LifecycleTransition(
            "post_mortem",
            "post_mortem_success",
            "done",
            department="devops",
            status="success",
            description="Successful deployment closes the project.",
        ),
        (
            "post_mortem",
            "post_mortem_failure",
            "devops",
            "failure",
        ): LifecycleTransition(
            "post_mortem",
            "post_mortem_failure",
            "development",
            department="devops",
            status="failure",
            description="Failed deployment routes back to Engineering.",
        ),
    }

    def __init__(self, state_manager):
        self.state = state_manager

    def apply(self, transition: Optional[LifecycleTransition]) -> Optional[str]:
        if transition is None:
            return None
        self.state.set_current_phase(transition.to_phase)
        return transition.to_phase

    def transition_for_deliverable(
        self, project: Project, deliverable: Deliverable
    ) -> Optional[LifecycleTransition]:
        event = (
            "deliverable_success"
            if deliverable.status == "success"
            else "deliverable_failure"
        )
        if project.phase == "qa" and deliverable.department == "qa":
            event = "deliverable_done"
        elif project.phase == "delivery" and deliverable.department == "delivery":
            event = "deliverable_done"
        elif project.phase == "deploying" and deliverable.department == "devops":
            event = "deliverable_done"
        return self.get_transition(
            project.phase, event, deliverable.department, deliverable.status
        )

    def transition_after_approval(
        self, project: Optional[Project], task: CompanyTask, approved: bool
    ) -> Optional[LifecycleTransition]:
        if project is None:
            return None
        context = task.context if isinstance(task.context, dict) else {}
        gate_name = context.get("gate_name")
        if gate_name == "release_approval":
            return self.get_transition(
                project.phase,
                "approval_approved" if approved else "approval_rejected",
                task.origin,
                None,
            )
        if not approved:
            return self.get_transition(
                project.phase, "approval_rejected", task.origin, None
            )
        if task.origin == "product" and task.target in {"engineering", "ux"}:
            event = "prd_approved_for_design" if task.target == "ux" else "prd_approved"
            return self.get_transition(project.phase, event, task.origin, "success")
        return None

    def transition_after_post_mortem(
        self, phase: str, deliverable: Deliverable
    ) -> Optional[LifecycleTransition]:
        event = (
            "post_mortem_success"
            if deliverable.status == "success"
            else "post_mortem_failure"
        )
        return self.get_transition(
            phase, event, deliverable.department, deliverable.status
        )

    def get_transition(
        self,
        phase: str,
        event: str,
        department: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[LifecycleTransition]:
        keys = (
            (phase, event, department, status),
            (phase, event, department, None),
            (phase, event, None, status),
            (phase, event, None, None),
        )
        for key in keys:
            transition = self.TRANSITIONS.get(key)
            if transition:
                return transition
        return None
