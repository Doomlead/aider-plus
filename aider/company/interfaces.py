from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


DeliverableStatus = Literal["success", "failure", "needs_review", "needs_revision"]


@dataclass
class Deliverable:
    """Typed artifact produced by a department."""

    task_id: str
    department: str
    artifact_type: str
    payload: Any
    status: DeliverableStatus
    phase: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    review_feedback: Any = None
    review_passed: Optional[bool] = None

    @property
    def content(self) -> Any:
        return self.payload

    @content.setter
    def content(self, value: Any) -> None:
        self.payload = value


@dataclass
class DepartmentOutput:
    """Normalized output envelope for department execution."""

    department: str
    deliverables: list[Deliverable] = field(default_factory=list)
    status: DeliverableStatus = "success"
    metadata: dict = field(default_factory=dict)


@dataclass
class ApprovalRequest:
    """Explicit approval gate contract for human-in-the-loop workflows."""

    task_id: str
    gate_name: str
    department: str
    approver_role: str
    artifact_preview: str
    target: Optional[str] = None
    phase: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ProcessResult:
    """Canonical result for a department process step."""

    output: DepartmentOutput
    approval_requests: list[ApprovalRequest] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_deliverable(cls, deliverable: Deliverable) -> "ProcessResult":
        return cls(
            output=DepartmentOutput(
                department=deliverable.department,
                deliverables=[deliverable],
                status=deliverable.status,
                metadata=dict(deliverable.metadata),
            )
        )
