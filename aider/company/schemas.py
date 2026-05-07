from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Union

from aider.company.interfaces import (
    ApprovalRequest,
    Deliverable,
    DepartmentOutput,
    ProcessResult,
)


class CompanyEvent(str, Enum):
    APPROVAL_REQUIRED = "approval_required"
    LIFECYCLE = "lifecycle"
    PROJECT_BLOCKED = "project_blocked"


@dataclass
class EventMessage:
    event: Union[CompanyEvent, str]
    task_id: str
    payload: dict
    metadata: dict = field(default_factory=dict)


@dataclass
class ApprovalDecision:
    approved: bool
    reason: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class QAFeedback:
    """
    Structured feedback from QA back to Engineering on a failed run.
    Passed as task.context["qa_feedback"] when orchestrator re-routes work.
    """

    test_passed: bool  # False when feedback is issued
    failed_tests: list[str]  # e.g. ["tests/test_auth.py::test_login"]
    failure_output: str  # raw pytest stderr/stdout for the failures
    files_covered: list[str]  # files QA actually checked
    recommended_fixes: list[str]  # human-readable suggestions for Engineering
    revision_number: int = 1  # increments each round-trip
    prd_excerpt: str = ""  # reminder of acceptance criteria

    def to_dict(self) -> dict:
        return {
            "test_passed": self.test_passed,
            "failed_tests": self.failed_tests,
            "failure_output": self.failure_output[:3000],  # cap for context injection
            "files_covered": self.files_covered,
            "recommended_fixes": self.recommended_fixes,
            "revision_number": self.revision_number,
            "prd_excerpt": self.prd_excerpt,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "QAFeedback":
        return cls(
            test_passed=bool(d.get("test_passed", False)),
            failed_tests=list(d.get("failed_tests", [])),
            failure_output=str(d.get("failure_output", "")),
            files_covered=list(d.get("files_covered", [])),
            recommended_fixes=list(d.get("recommended_fixes", [])),
            revision_number=int(d.get("revision_number", 1)),
            prd_excerpt=str(d.get("prd_excerpt", "")),
        )


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


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "CompanyEvent",
    "CompanyTask",
    "Deliverable",
    "DepartmentOutput",
    "EventMessage",
    "ProcessResult",
    "QAFeedback",
]
