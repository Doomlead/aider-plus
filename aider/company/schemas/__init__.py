import json as _json

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Union

from aider.company.schemas.design_spec import DesignSpecV2
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
    revision_count: int = 0
    previous_prd_summary: Optional[str] = None

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
            "revision_count": self.revision_count,
            "previous_prd_summary": self.previous_prd_summary,
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
            revision_count=int(d.get("revision_count", 0) or 0),
            previous_prd_summary=(
                str(d.get("previous_prd_summary"))
                if d.get("previous_prd_summary") is not None
                else None
            ),
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


def _markdown_bullets(items: list[str], empty: str = "TBD") -> str:
    return "\n".join(f"- {item}" for item in items) if items else f"- {empty}"


@dataclass
class Milestone:
    """Delivery milestone with status and ownership metadata."""

    name: str
    description: str = ""
    owner: str = "delivery"
    status: str = "pending"
    due: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)
    exit_criteria: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        due = f" (due: {self.due})" if self.due else ""
        return (
            f"### {self.name}{due}\n"
            f"- **Owner:** {self.owner}\n"
            f"- **Status:** {self.status}\n"
            f"- **Description:** {self.description or 'TBD'}\n"
            f"- **Dependencies:** {', '.join(self.dependencies) if self.dependencies else 'None'}\n"
            f"- **Exit criteria:**\n{_markdown_bullets(self.exit_criteria)}"
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "owner": self.owner,
            "status": self.status,
            "due": self.due,
            "dependencies": self.dependencies,
            "exit_criteria": self.exit_criteria,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Milestone":
        return cls(
            name=str(d.get("name", "Milestone")),
            description=str(d.get("description", "")),
            owner=str(d.get("owner", "delivery")),
            status=str(d.get("status", "pending")),
            due=str(d.get("due")) if d.get("due") is not None else None,
            dependencies=list(d.get("dependencies", [])),
            exit_criteria=list(d.get("exit_criteria", [])),
        )


@dataclass
class RiskRegister:
    """Structured delivery risk entry for tracking probability, impact, and mitigation."""

    risk_id: str
    description: str
    severity: str = "medium"
    probability: str = "medium"
    impact: str = "medium"
    owner: str = "delivery"
    mitigation: str = ""
    status: str = "open"
    blockers: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        return (
            f"### {self.risk_id}: {self.description}\n"
            f"- **Severity:** {self.severity}\n"
            f"- **Probability:** {self.probability}\n"
            f"- **Impact:** {self.impact}\n"
            f"- **Owner:** {self.owner}\n"
            f"- **Status:** {self.status}\n"
            f"- **Mitigation:** {self.mitigation or 'TBD'}\n"
            f"- **Blockers:** {', '.join(self.blockers) if self.blockers else 'None'}"
        )

    def to_dict(self) -> dict:
        return {
            "risk_id": self.risk_id,
            "description": self.description,
            "severity": self.severity,
            "probability": self.probability,
            "impact": self.impact,
            "owner": self.owner,
            "mitigation": self.mitigation,
            "status": self.status,
            "blockers": self.blockers,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RiskRegister":
        return cls(
            risk_id=str(d.get("risk_id", d.get("id", "RISK-1"))),
            description=str(d.get("description", "Delivery risk")),
            severity=str(d.get("severity", "medium")),
            probability=str(d.get("probability", "medium")),
            impact=str(d.get("impact", "medium")),
            owner=str(d.get("owner", "delivery")),
            mitigation=str(d.get("mitigation", "")),
            status=str(d.get("status", "open")),
            blockers=list(d.get("blockers", [])),
        )


@dataclass
class Timeline:
    """Project timeline summary owned by Delivery / Project Management."""

    summary: str
    start: Optional[str] = None
    target_release: Optional[str] = None
    cadence: str = "daily async check-in"
    milestones: list[Milestone] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        milestones = "\n\n".join(m.to_markdown() for m in self.milestones) or "- TBD"
        return f"""## Timeline
{self.summary}

- **Start:** {self.start or 'TBD'}
- **Target release:** {self.target_release or 'TBD'}
- **Cadence:** {self.cadence}

### Assumptions
{_markdown_bullets(self.assumptions)}

### Milestones
{milestones}
"""

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "start": self.start,
            "target_release": self.target_release,
            "cadence": self.cadence,
            "milestones": [m.to_dict() for m in self.milestones],
            "assumptions": self.assumptions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        return cls(
            summary=str(d.get("summary", "Delivery timeline")),
            start=str(d.get("start")) if d.get("start") is not None else None,
            target_release=(
                str(d.get("target_release")) if d.get("target_release") is not None else None
            ),
            cadence=str(d.get("cadence", "daily async check-in")),
            milestones=[Milestone.from_dict(m) for m in d.get("milestones", [])],
            assumptions=list(d.get("assumptions", [])),
        )


@dataclass
class ProjectPlan:
    """Delivery-owned plan that coordinates scope, milestones, timeline, and risks."""

    title: str
    objective: str
    milestones: list[Milestone] = field(default_factory=list)
    risks: list[RiskRegister] = field(default_factory=list)
    timeline: Optional[Timeline] = None
    dependencies: list[str] = field(default_factory=list)
    cross_department_alignment: list[str] = field(default_factory=list)
    status: str = "on_track"
    progress_summary: str = ""
    version: str = "1.0"

    def to_markdown(self) -> str:
        milestones = "\n\n".join(m.to_markdown() for m in self.milestones) or "- TBD"
        risks = "\n\n".join(r.to_markdown() for r in self.risks) or "- None identified"
        timeline = self.timeline.to_markdown() if self.timeline else "## Timeline\n- TBD"
        return f"""# Delivery Plan: {self.title}
**Version:** {self.version}  **Status:** {self.status}

## Objective
{self.objective}

## Progress Summary
{self.progress_summary or 'Initial plan created.'}

## Cross-Department Alignment
{_markdown_bullets(self.cross_department_alignment)}

## Dependencies
{_markdown_bullets(self.dependencies, empty='None')}

{timeline}

## Milestone Tracker
{milestones}

## Risk Register
{risks}
"""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "objective": self.objective,
            "milestones": [m.to_dict() for m in self.milestones],
            "risks": [r.to_dict() for r in self.risks],
            "timeline": self.timeline.to_dict() if self.timeline else None,
            "dependencies": self.dependencies,
            "cross_department_alignment": self.cross_department_alignment,
            "status": self.status,
            "progress_summary": self.progress_summary,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectPlan":
        timeline = d.get("timeline")
        return cls(
            title=str(d.get("title", "Delivery Plan")),
            objective=str(d.get("objective", "Coordinate delivery.")),
            milestones=[Milestone.from_dict(m) for m in d.get("milestones", [])],
            risks=[RiskRegister.from_dict(r) for r in d.get("risks", [])],
            timeline=Timeline.from_dict(timeline) if isinstance(timeline, dict) else None,
            dependencies=list(d.get("dependencies", [])),
            cross_department_alignment=list(d.get("cross_department_alignment", [])),
            status=str(d.get("status", "on_track")),
            progress_summary=str(d.get("progress_summary", "")),
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
        "delivery_plan",
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
    "DesignSpecV2",
    "Deliverable",
    "DepartmentOutput",
    "EventMessage",
    "Milestone",
    "ProjectPlan",
    "PRD",
    "ProcessResult",
    "QAFeedback",
    "RiskRegister",
    "Timeline",
]
