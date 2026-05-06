from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional, Any

from aider.company.interfaces import (
    ApprovalRequest,
    Deliverable,
    DepartmentOutput,
    ProcessResult,
)


class CompanyEvent(str, Enum):
    APPROVAL_REQUIRED = "approval_required"


@dataclass
class EventMessage:
    event: CompanyEvent
    task_id: str
    payload: dict
    metadata: dict = field(default_factory=dict)


@dataclass
class ApprovalDecision:
    approved: bool
    reason: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class CompanyTask:
    task_id: str
    origin: str  # e.g. "ceo", "product"
    target: str  # department name
    artifact_type: Literal[
        "raw_prompt",
        "prd",
        "design_spec",
        "code",
        "test_report",
        "deploy_request",
        "memo",
        "general",
    ]
    payload: Any
    blocking: bool = False
    context: dict = field(default_factory=dict)
