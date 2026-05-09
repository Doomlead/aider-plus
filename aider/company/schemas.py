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
class PRD:
    """
    Structured Product Requirements Document.

    All list fields default to empty so callers can build incrementally.
    The ``to_markdown()`` method produces the artifact preview string that
    gets injected into Engineering / UX context.
    """

    title: str
    problem_statement: str
    goals: list[str] = field(default_factory=list)
    success_metrics: list[str] = field(default_factory=list)
    user_stories: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    technical_considerations: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    priority: str = "MVP"
    open_questions: list[str] = field(default_factory=list)
    version: str = "1.0"

    def to_markdown(self) -> str:
        """Return the PRD as a Markdown string for context injection."""

        def _bullet(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "- TBD"

        return f"""# PRD: {self.title}
**Version:** {self.version}  **Priority:** {self.priority}

## Problem Statement
{self.problem_statement}

## Goals
{_bullet(self.goals)}

## Success Metrics
{_bullet(self.success_metrics)}

## User Stories
{_bullet(self.user_stories)}

## Acceptance Criteria
{_bullet(self.acceptance_criteria)}

## Technical Considerations
{_bullet(self.technical_considerations)}

## Out of Scope
{_bullet(self.out_of_scope)}

## Open Questions
{_bullet(self.open_questions) if self.open_questions else "None"}
"""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "problem_statement": self.problem_statement,
            "goals": self.goals,
            "success_metrics": self.success_metrics,
            "user_stories": self.user_stories,
            "acceptance_criteria": self.acceptance_criteria,
            "technical_considerations": self.technical_considerations,
            "out_of_scope": self.out_of_scope,
            "priority": self.priority,
            "open_questions": self.open_questions,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PRD":
        return cls(
            title=str(d.get("title", "Untitled")),
            problem_statement=str(d.get("problem_statement", "")),
            goals=list(d.get("goals", [])),
            success_metrics=list(d.get("success_metrics", [])),
            user_stories=list(d.get("user_stories", [])),
            acceptance_criteria=list(d.get("acceptance_criteria", [])),
            technical_considerations=list(d.get("technical_considerations", [])),
            out_of_scope=list(d.get("out_of_scope", [])),
            priority=str(d.get("priority", "MVP")),
            open_questions=list(d.get("open_questions", [])),
            version=str(d.get("version", "1.0")),
        )


@dataclass
class ClarificationRequest:
    """
    Wraps a set of clarification questions from Product to the CEO.
    Stored in task.context["clarification_request"] for approval recovery.
    """

    questions: list[str]
    original_request: str
    task_id: str

    def to_dict(self) -> dict:
        return {
            "questions": self.questions,
            "original_request": self.original_request,
            "task_id": self.task_id,
        }

    def format_for_approval(self) -> str:
        qs = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(self.questions))
        return (
            "**Product needs clarification before writing the PRD.**\n\n"
            f"**Original request:** {self.original_request[:500]}\n\n"
            f"**Questions:**\n{qs}\n\n"
            "*Please answer these questions and approve to continue.*"
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
        "clarification",
        "general",
    ]
    payload: Any
    blocking: bool = False
    context: dict = field(default_factory=dict)


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "CompanyEvent",
    "ClarificationRequest",
    "CompanyTask",
    "Deliverable",
    "DepartmentOutput",
    "EventMessage",
    "PRD",
    "ProcessResult",
    "QAFeedback",
]
