import json as _json

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
class DesignSpec:
    """
    Structured design deliverable from UX to Engineering.

    to_markdown() is injected into Engineering context as prd_content companion.
    to_dict() / from_dict() round-trip through project memory and handoff payloads.
    """

    title: str
    overview: str
    key_screens: list[str] = field(default_factory=list)
    component_library: list[dict] = field(default_factory=list)
    user_flows: list[str] = field(default_factory=list)
    accessibility_notes: list[str] = field(default_factory=list)
    technical_requirements: list[str] = field(default_factory=list)
    visual_style: dict = field(default_factory=dict)
    version: str = "1.0"

    def to_markdown(self) -> str:
        def _bullet(items: list) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "- None"

        components = (
            "\n".join(
                f"**{c['name']}**: {c.get('description', '')}"
                for c in self.component_library
                if isinstance(c, dict) and c.get("name")
            )
            if self.component_library
            else "None defined"
        )
        style_text = (
            _json.dumps(self.visual_style, indent=2) if self.visual_style else "Default theme"
        )
        combined_notes = self.accessibility_notes + self.technical_requirements
        return (
            f"# Design Spec: {self.title}\n"
            f"**Version:** {self.version}\n\n"
            f"## Overview\n{self.overview}\n\n"
            f"## Key Screens / Pages\n{_bullet(self.key_screens)}\n\n"
            f"## Component Library\n{components}\n\n"
            f"## User Flows\n{_bullet(self.user_flows)}\n\n"
            f"## Accessibility & Technical Notes\n{_bullet(combined_notes)}\n\n"
            f"## Visual Style Guidelines\n{style_text}\n"
        )

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "overview": self.overview,
            "key_screens": self.key_screens,
            "component_library": self.component_library,
            "user_flows": self.user_flows,
            "accessibility_notes": self.accessibility_notes,
            "technical_requirements": self.technical_requirements,
            "visual_style": self.visual_style,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DesignSpec":
        return cls(
            title=str(d.get("title", "Untitled Design")),
            overview=str(d.get("overview", "")),
            key_screens=list(d.get("key_screens", [])),
            component_library=list(d.get("component_library", [])),
            user_flows=list(d.get("user_flows", [])),
            accessibility_notes=list(d.get("accessibility_notes", [])),
            technical_requirements=list(d.get("technical_requirements", [])),
            visual_style=dict(d.get("visual_style", {})),
            version=str(d.get("version", "1.0")),
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
    "DesignSpec",
    "Deliverable",
    "DepartmentOutput",
    "EventMessage",
    "PRD",
    "ProcessResult",
    "QAFeedback",
]
