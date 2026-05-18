import json as _json

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional, Union, List

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
            _json.dumps(self.visual_style, indent=2)
            if self.visual_style
            else "Default theme"
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
                str(d.get("target_release"))
                if d.get("target_release") is not None
                else None
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
    key_dependencies: list[str] = field(default_factory=list)
    cross_department_alignment: list[str] = field(default_factory=list)
    status: Literal["on_track", "at_risk", "delayed", "complete"] = "on_track"
    overall_status: str = "on_track"
    completion_percentage: int = 0
    weighted_completion: int = 0
    executive_summary: str = ""
    critical_blockers: list[str] = field(default_factory=list)
    next_milestone: Optional[str] = None
    progress_summary: str = ""
    version: str = "1.0"

    def to_markdown(self) -> str:
        milestones = "\n\n".join(m.to_markdown() for m in self.milestones) or "- TBD"
        risks = "\n\n".join(r.to_markdown() for r in self.risks) or "- None identified"
        timeline = (
            self.timeline.to_markdown() if self.timeline else "## Timeline\n- TBD"
        )
        return f"""# Delivery Plan: {self.title}
**Version:** {self.version}  **Status:** {self.status}  **Weighted completion:** {self.weighted_completion or self.completion_percentage}%

## Executive Summary
{self.executive_summary or self.progress_summary or 'Delivery is coordinating scope, risks, and release readiness.'}

## Objective
{self.objective}

## Delivery Dashboard
- **Status:** {self.status}
- **Completion:** {self.weighted_completion or self.completion_percentage}%
- **Next milestone:** {self.next_milestone or 'TBD'}
- **Critical blockers:** {', '.join(self.critical_blockers) if self.critical_blockers else 'None'}

## Progress Summary
{self.progress_summary or 'Initial plan created.'}

## Cross-Department Alignment
{_markdown_bullets(self.cross_department_alignment)}

## Key Dependencies
{_markdown_bullets(self.key_dependencies or self.dependencies, empty='None')}

## All Dependencies
{_markdown_bullets(self.dependencies, empty='None')}

{timeline}

## Milestone Tracker
{milestones}

## Risk Register
{risks}
"""

    def to_summary(self) -> dict:
        """Return the compact delivery dashboard summary."""
        return {
            "title": self.title,
            "overall_status": self.overall_status or self.status,
            "status": self.status,
            "completion_percentage": self.completion_percentage,
            "weighted_completion": self.weighted_completion
            or self.completion_percentage,
            "executive_summary": self.executive_summary,
            "next_milestone": self.next_milestone or "TBD",
            "critical_blockers": list(self.critical_blockers),
            "risk_count": len(self.risks),
            "milestone_count": len(self.milestones),
            "progress_summary": self.progress_summary,
        }

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "objective": self.objective,
            "milestones": [m.to_dict() for m in self.milestones],
            "risks": [r.to_dict() for r in self.risks],
            "timeline": self.timeline.to_dict() if self.timeline else None,
            "dependencies": self.dependencies,
            "key_dependencies": self.key_dependencies,
            "cross_department_alignment": self.cross_department_alignment,
            "status": self.status,
            "overall_status": self.overall_status,
            "completion_percentage": self.completion_percentage,
            "weighted_completion": self.weighted_completion
            or self.completion_percentage,
            "executive_summary": self.executive_summary,
            "critical_blockers": self.critical_blockers,
            "next_milestone": self.next_milestone,
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
            timeline=(
                Timeline.from_dict(timeline) if isinstance(timeline, dict) else None
            ),
            dependencies=list(d.get("dependencies", [])),
            key_dependencies=list(d.get("key_dependencies", d.get("dependencies", []))),
            cross_department_alignment=list(d.get("cross_department_alignment", [])),
            status=cls._coerce_status(str(d.get("status", "on_track"))),
            overall_status=str(d.get("overall_status", d.get("status", "on_track"))),
            completion_percentage=int(d.get("completion_percentage", 0) or 0),
            weighted_completion=int(
                d.get("weighted_completion", d.get("completion_percentage", 0)) or 0
            ),
            executive_summary=str(d.get("executive_summary", "")),
            critical_blockers=list(d.get("critical_blockers", [])),
            next_milestone=(
                str(d.get("next_milestone"))
                if d.get("next_milestone") is not None
                else None
            ),
            progress_summary=str(d.get("progress_summary", "")),
            version=str(d.get("version", "1.0")),
        )

    @staticmethod
    def _coerce_status(
        status: str,
    ) -> Literal["on_track", "at_risk", "delayed", "complete"]:
        mapping = {
            "blocked": "delayed",
            "release_ready": "complete",
            "planning": "on_track",
            "ready": "complete",
        }
        normalized = mapping.get(status, status)
        if normalized in {"on_track", "at_risk", "delayed", "complete"}:
            return normalized  # type: ignore[return-value]
        return "on_track"


@dataclass
class BuildArtifact:
    """Artifact produced by DevOps build/package execution."""

    artifact_type: Literal[
        "docker_image",
        "pypi_package",
        "executable",
        "static_site",
        "source_snapshot",
        "unknown",
    ]
    name: str
    version: str
    tag: str
    location: str
    build_logs_summary: str = ""
    log_artifacts: list[str] = field(default_factory=list)
    detected_command_source: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "artifact_type": self.artifact_type,
            "name": self.name,
            "version": self.version,
            "tag": self.tag,
            "location": self.location,
            "build_logs_summary": self.build_logs_summary,
            "log_artifacts": list(self.log_artifacts),
            "detected_command_source": self.detected_command_source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BuildArtifact":
        return cls(
            artifact_type=str(d.get("artifact_type", "unknown")),
            name=str(d.get("name", "artifact")),
            version=str(d.get("version", "1.0.0")),
            tag=str(d.get("tag", "v1.0.0")),
            location=str(d.get("location", "")),
            build_logs_summary=str(d.get("build_logs_summary", "")),
            log_artifacts=list(d.get("log_artifacts", []) or []),
            detected_command_source=str(d.get("detected_command_source", "unknown")),
        )


@dataclass
class DeploymentTarget:
    """Provider/environment/config tuple for DevOps deployment execution."""

    provider: str
    environment: str = "production"
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.provider = str(self.provider or "local")
        self.environment = str(self.environment or "production")
        self.config = dict(self.config or {})

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "environment": self.environment,
            "config": dict(self.config),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "DeploymentTarget":
        data = d or {}
        config = data.get("config") or {}
        return cls(
            provider=str(data.get("provider") or "local"),
            environment=str(data.get("environment") or "production"),
            config=dict(config) if isinstance(config, dict) else {},
        )


@dataclass
class DeploymentResult:
    """Result of DevOps deployment execution for a built artifact."""

    environment: str
    status: Literal["success", "failed", "partial"]
    build_artifact: BuildArtifact
    deployed_url: Optional[str] = None
    rollback_url: Optional[str] = None
    target: Optional[DeploymentTarget] = None
    logs_url: Optional[str] = None
    rollback_command: Optional[str] = None
    deployment_notes: str = ""
    deployed_at: Optional[str] = None
    deployment_logs: str = ""
    log_artifacts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.target, dict):
            self.target = DeploymentTarget.from_dict(self.target)

    def to_dict(self) -> dict:
        return {
            "environment": self.environment,
            "status": self.status,
            "target": self.target.to_dict() if self.target else None,
            "deployed_url": self.deployed_url,
            "logs_url": self.logs_url,
            "rollback_url": self.rollback_url,
            "rollback_command": self.rollback_command,
            "deployment_notes": self.deployment_notes,
            "deployed_at": self.deployed_at,
            "build_artifact": self.build_artifact.to_dict(),
            "deployment_logs": self.deployment_logs,
            "log_artifacts": list(self.log_artifacts),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DeploymentResult":
        artifact = d.get("build_artifact") or {}
        target = d.get("target")
        return cls(
            environment=str(d.get("environment", "production")),
            status=str(d.get("status", "failed")),
            target=(
                DeploymentTarget.from_dict(target)
                if isinstance(target, dict)
                else target
            ),
            deployed_url=(
                str(d.get("deployed_url"))
                if d.get("deployed_url") is not None
                else None
            ),
            logs_url=(
                str(d.get("logs_url")) if d.get("logs_url") is not None else None
            ),
            rollback_url=(
                str(d.get("rollback_url"))
                if d.get("rollback_url") is not None
                else None
            ),
            rollback_command=(
                str(d.get("rollback_command"))
                if d.get("rollback_command") is not None
                else None
            ),
            deployment_notes=str(d.get("deployment_notes", "")),
            deployed_at=(
                str(d.get("deployed_at")) if d.get("deployed_at") is not None else None
            ),
            build_artifact=(
                BuildArtifact.from_dict(artifact)
                if isinstance(artifact, dict)
                else artifact
            ),
            deployment_logs=str(d.get("deployment_logs", "")),
            log_artifacts=list(d.get("log_artifacts", []) or []),
        )


@dataclass
class DeliveryHandover:
    """Explicit release-readiness artifact handed from Delivery to DevOps."""

    project_name: str
    ready_for_devops: bool
    delivery_summary: dict
    release_scope: str = ""
    critical_blockers: list[str] = field(default_factory=list)
    rollback_notes: list[str] = field(default_factory=list)
    go_no_go_recommendation: str = "NO-GO until Delivery confirms readiness."
    release_notes_draft: str = ""
    rollback_plan: str = ""
    environment: str = "production"
    deployment_target: Optional[DeploymentTarget] = None

    def __post_init__(self) -> None:
        if isinstance(self.deployment_target, dict):
            self.deployment_target = DeploymentTarget.from_dict(self.deployment_target)

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "ready_for_devops": self.ready_for_devops,
            "delivery_summary": self.delivery_summary,
            "release_scope": self.release_scope,
            "critical_blockers": self.critical_blockers,
            "rollback_notes": self.rollback_notes,
            "go_no_go_recommendation": self.go_no_go_recommendation,
            "release_notes_draft": self.release_notes_draft,
            "rollback_plan": self.rollback_plan,
            "environment": self.environment,
            "deployment_target": (
                self.deployment_target.to_dict() if self.deployment_target else None
            ),
        }

    def to_markdown(self) -> str:
        blockers = _markdown_bullets(self.critical_blockers, empty="None")
        rollback = _markdown_bullets(
            self.rollback_notes, empty="Confirm rollback owner and steps"
        )
        return f"""# Delivery → DevOps Handover: {self.project_name}
**Ready for DevOps:** {'yes' if self.ready_for_devops else 'no'}
**Go / No-Go:** {self.go_no_go_recommendation}
**Environment:** {self.environment}
**Deployment Target:** {self.deployment_target.provider if self.deployment_target else 'local'}

## Release Scope
{self.release_scope or 'TBD'}

## Delivery Summary
- **Status:** {self.delivery_summary.get('overall_status', 'unknown')}
- **Completion:** {self.delivery_summary.get('completion_percentage', 0)}%
- **Next milestone:** {self.delivery_summary.get('next_milestone', 'TBD')}

## Release Notes Draft
{self.release_notes_draft or 'TBD'}

## Critical Blockers
{blockers}

## Rollback Plan
{self.rollback_plan or 'Confirm deployment owner, previous artifact, database rollback path, and validation smoke tests.'}

## Rollback Notes
{rollback}
"""

    @classmethod
    def from_dict(cls, d: dict) -> "DeliveryHandover":
        return cls(
            project_name=str(d.get("project_name", "Project")),
            ready_for_devops=bool(d.get("ready_for_devops", False)),
            delivery_summary=dict(d.get("delivery_summary", {})),
            release_scope=str(d.get("release_scope", "")),
            critical_blockers=list(d.get("critical_blockers", [])),
            rollback_notes=list(d.get("rollback_notes", [])),
            go_no_go_recommendation=str(
                d.get(
                    "go_no_go_recommendation",
                    "NO-GO until Delivery confirms readiness.",
                )
            ),
            release_notes_draft=str(d.get("release_notes_draft", "")),
            rollback_plan=str(d.get("rollback_plan", "")),
            environment=str(d.get("environment", "production")),
            deployment_target=(
                DeploymentTarget.from_dict(d.get("deployment_target"))
                if isinstance(d.get("deployment_target"), dict)
                else None
            ),
        )


@dataclass(frozen=True)
class ProofOfWork:
    """Structured proof artifact produced by Company daemon issue runs."""

    issue: str
    title: str
    workspace: str
    branch: str | None = None
    pr_url: str | None = None
    summary: str = ""
    changed_files: tuple[str, ...] = ()
    diff_summary: tuple[str, ...] = ()
    commit_messages: tuple[str, ...] = ()
    checks: tuple[dict[str, Any], ...] = ()
    qa_result: str = "not-run"
    review_result: str = "not-run"
    review_feedback: tuple[str, ...] = ()
    delivery_handover: dict[str, Any] = field(default_factory=dict)
    devops_status: dict[str, Any] = field(default_factory=dict)
    diffs: tuple[dict[str, str], ...] = ()
    links: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()
    completed_stages: tuple[str, ...] = ()
    failed_stages: tuple[str, ...] = ()
    partial_stages: tuple[str, ...] = ()
    retry_count: int = 0
    last_error: str | None = None
    partial_success: bool = False
    human_review_required: bool = True
    markdown_path: str | None = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "title": self.title,
            "workspace": self.workspace,
            "branch": self.branch,
            "pr_url": self.pr_url,
            "summary": self.summary,
            "changed_files": list(self.changed_files),
            "diff_summary": list(self.diff_summary),
            "commit_messages": list(self.commit_messages),
            "checks": list(self.checks),
            "qa_result": self.qa_result,
            "review_result": self.review_result,
            "review_feedback": list(self.review_feedback),
            "delivery_handover": dict(self.delivery_handover),
            "devops_status": dict(self.devops_status),
            "diffs": list(self.diffs),
            "links": list(self.links),
            "risk_notes": list(self.risk_notes),
            "completed_stages": list(self.completed_stages),
            "failed_stages": list(self.failed_stages),
            "partial_stages": list(self.partial_stages),
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "partial_success": self.partial_success,
            "human_review_required": self.human_review_required,
            "markdown_path": self.markdown_path,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProofOfWork":
        return cls(
            issue=str(data.get("issue", "")),
            title=str(data.get("title", "")),
            workspace=str(data.get("workspace", "")),
            branch=str(data.get("branch")) if data.get("branch") else None,
            pr_url=str(data.get("pr_url")) if data.get("pr_url") else None,
            summary=str(data.get("summary", "")),
            changed_files=tuple(str(item) for item in data.get("changed_files", ())),
            diff_summary=tuple(str(item) for item in data.get("diff_summary", ())),
            commit_messages=tuple(
                str(item) for item in data.get("commit_messages", ())
            ),
            checks=tuple(dict(item) for item in data.get("checks", ())),
            qa_result=str(data.get("qa_result", "not-run")),
            review_result=str(data.get("review_result", "not-run")),
            review_feedback=tuple(
                str(item) for item in data.get("review_feedback", ())
            ),
            delivery_handover=dict(data.get("delivery_handover") or {}),
            devops_status=dict(data.get("devops_status") or {}),
            diffs=tuple(dict(item) for item in data.get("diffs", ())),
            links=tuple(str(item) for item in data.get("links", ())),
            risk_notes=tuple(str(item) for item in data.get("risk_notes", ())),
            completed_stages=tuple(
                str(item) for item in data.get("completed_stages", ())
            ),
            failed_stages=tuple(str(item) for item in data.get("failed_stages", ())),
            partial_stages=tuple(str(item) for item in data.get("partial_stages", ())),
            retry_count=int(data.get("retry_count", 0) or 0),
            last_error=(
                str(data.get("last_error"))
                if data.get("last_error") is not None
                else None
            ),
            partial_success=bool(data.get("partial_success", False)),
            human_review_required=bool(data.get("human_review_required", True)),
            markdown_path=(
                str(data.get("markdown_path")) if data.get("markdown_path") else None
            ),
            created_at=str(data.get("created_at") or datetime.utcnow().isoformat()),
        )

    def to_markdown(self) -> str:
        def bullets(items: tuple[str, ...] | list[str], empty: str = "None") -> str:
            return "\n".join(f"- {item}" for item in items) if items else f"- {empty}"

        checks = []
        for check in self.checks:
            command = check.get("command") or check.get("name") or "check"
            status = check.get("status") or check.get("result") or "unknown"
            checks.append(f"{command}: {status}")
        diff_lines = list(self.diff_summary)
        if not diff_lines and self.changed_files:
            diff_lines = [f"{file} — changed" for file in self.changed_files]
        links = list(self.links)
        if self.pr_url and self.pr_url not in links:
            links.insert(0, self.pr_url)
        retry_note = f"Retry count: {self.retry_count}"
        if self.last_error:
            retry_note += f"; last error: {self.last_error}"
        tldr = self.summary or "No summary provided."
        if self.partial_success:
            tldr += " Partial success; review failed or partial stages before merging."
        elif self.human_review_required:
            tldr += " Human review is required before this work is considered complete."
        else:
            tldr += " No human review blockers were detected."
        return f"""# Proof of Work: {self.issue}

## Executive TL;DR
{tldr}

**Title:** {self.title}
**Created:** {self.created_at}
**Workspace:** `{self.workspace}`
**Branch:** {self.branch or "unknown"}
**Human review required:** {self.human_review_required}
**Partial success:** {self.partial_success}
**{retry_note}**

## Summary
{self.summary or "No summary provided."}

## Stage Status

### Completed Stages
{bullets(self.completed_stages)}

### Failed Stages
{bullets(self.failed_stages)}

### Partial / Attempted Stages
{bullets(self.partial_stages)}

## Changed Files
{bullets(self.changed_files)}

## Diff Summary
{bullets(diff_lines)}

## Commits
{bullets(self.commit_messages)}

## QA Results
**Status:** {self.qa_result}

{bullets(checks)}

## Review Feedback
**Status:** {self.review_result}

{bullets(self.review_feedback)}

## Delivery Handover
```json
{_json.dumps(self.delivery_handover, indent=2, sort_keys=True)}
```

## DevOps Build/Deploy
```json
{_json.dumps(self.devops_status, indent=2, sort_keys=True)}
```

## Links
{bullets(links)}

## Risks / Follow-ups
{bullets(self.risk_notes)}

## Diffs
Full diffs are stored in the JSON proof artifact when available. This Markdown report keeps a concise diff summary to stay reviewable.
""".strip() + "\n"


@dataclass
class SecurityScanResult:
    scan_type: Literal["vuln", "pentest", "code_review", "platform_audit"]
    severity: Literal["critical", "high", "medium", "low", "info"]
    findings: List[dict]  # {location, description, recommendation, cve?}
    fixed_count: int = 0
    risk_score: float = 0.0
    raw_output_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "scan_type": self.scan_type,
            "severity": self.severity,
            "findings": list(self.findings),
            "fixed_count": self.fixed_count,
            "risk_score": self.risk_score,
            "raw_output_summary": self.raw_output_summary,
        }

    def to_markdown(self) -> str:
        findings = self.findings or []
        finding_lines = []
        for finding in findings:
            if not isinstance(finding, dict):
                finding_lines.append(f"- {finding}")
                continue
            finding_lines.append(
                "- "
                f"{finding.get('id') or finding.get('location') or 'finding'}: "
                f"{finding.get('description') or 'No description'} "
                f"(recommendation: {finding.get('recommendation') or 'TBD'})"
            )
        return (
            "# Security Report\n\n"
            f"- Scan type: {self.scan_type}\n"
            f"- Severity: {self.severity}\n"
            f"- Risk score: {self.risk_score}\n"
            f"- Fixed count: {self.fixed_count}\n\n"
            "## Findings\n"
            f"{chr(10).join(finding_lines) if finding_lines else '- None'}\n\n"
            "## Raw output summary\n"
            f"{self.raw_output_summary or 'None'}\n"
        )


@dataclass
class SecurityPatchRequest:
    finding_id: str
    patch_plan: str
    target_department: Literal["engineering", "architect"]
    urgency: Literal["immediate", "scheduled"]
    vulnerability_description: str = ""
    recommended_fix: str = ""
    suggested_code_change_summary: str = ""
    architect_prompt_seed: str = ""
    full_context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "patch_plan": self.patch_plan,
            "target_department": self.target_department,
            "urgency": self.urgency,
            "vulnerability_description": self.vulnerability_description,
            "recommended_fix": self.recommended_fix or self.patch_plan,
            "suggested_code_change_summary": self.suggested_code_change_summary
            or self.patch_plan,
            "architect_prompt_seed": self.architect_prompt_seed,
            "full_context": dict(self.full_context),
        }


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
        "security_scan_result",
        "security_patch_request",
        "general",
    ]
    payload: Any
    blocking: bool = False
    context: dict = field(default_factory=dict)


__all__ = [
    "ApprovalDecision",
    "BuildArtifact",
    "ApprovalRequest",
    "CompanyEvent",
    "ClarificationRequest",
    "CompanyTask",
    "DesignSpec",
    "DesignSpecV2",
    "Deliverable",
    "DeliveryHandover",
    "DeploymentTarget",
    "DepartmentOutput",
    "DeploymentResult",
    "EventMessage",
    "Milestone",
    "ProjectPlan",
    "ProofOfWork",
    "PRD",
    "ProcessResult",
    "QAFeedback",
    "RiskRegister",
    "SecurityPatchRequest",
    "SecurityScanResult",
    "Timeline",
]
