from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from aider.company.schemas import CompanyTask
from aider.company.state import CompanyStateManager
from aider.skills import SkillManager, SkillSummary

COMPANY_SKILL_SCOPES = ("shared", "coo", "product", "ux", "engineering", "reviewer", "qa", "devops")
DEFAULT_SKILL_QUERY_K = 5


@dataclass
class SkillLearningConfig:
    """Risk controls for procedural-memory learning.

    By default the learner writes approval-gated proposals instead of mutating
    executable long-lived skills. Operators can opt into direct create/patch in
    trusted local repos, but preserving current playbook learning requires no
    special configuration.
    """

    enabled: bool = True
    auto_create: bool = False
    auto_patch: bool = False
    require_human_approval: bool = True
    min_successful_repetitions: int = 2
    min_tool_calls: int = 5
    max_skills_per_role: int = 100
    query_k: int = DEFAULT_SKILL_QUERY_K


@dataclass
class SkillProposal:
    proposal_id: str
    action: str
    scope: str
    name: str
    title: str
    content: str
    rationale: str
    source_tasks: list[str] = field(default_factory=list)
    source_audit_events: list[str] = field(default_factory=list)
    confidence: float = 0.5
    status: str = "pending"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillProposal":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        payload = {key: value for key, value in data.items() if key in allowed}
        return cls(**payload)


class CompanySkillManager:
    """Role-aware skill retrieval and approval-gated procedural memory."""

    def __init__(self, state: CompanyStateManager, config: Optional[SkillLearningConfig] = None):
        self.state = state
        self.config = config or SkillLearningConfig()
        root = Path(state.memory.repo_path) / ".aider" / "skills"
        self.manager = SkillManager(root, max_skills_per_scope=self.config.max_skills_per_role)
        self.proposals_root = Path(state.memory.repo_path) / ".aider" / "skill_proposals"

    def scopes_for_role(self, role: str | None) -> list[str]:
        scopes = ["shared"]
        normalized = str(role or "").lower().strip()
        if normalized in COMPANY_SKILL_SCOPES and normalized != "shared":
            scopes.append(normalized)
        return scopes

    def query_for_task(self, task: CompanyTask, *, role: str | None = None) -> list[SkillSummary]:
        query = self._task_query(task)
        return self.manager.query_skills(
            query,
            scopes=self.scopes_for_role(role or task.target),
            k=self.config.query_k,
            min_score=0.05,
        )

    def format_skill_guidance(self, skills: Iterable[SkillSummary]) -> list[str]:
        guidance: list[str] = []
        for skill in skills:
            summary = skill.description or skill.title or "No summary available"
            guidance.append(
                f"{skill.scope}/{skill.name}: {skill.title} — {summary}".strip()
            )
        return guidance

    def record_skill_usage(
        self, skills: Iterable[SkillSummary], *, role: str | None = None
    ) -> None:
        used = list(skills)
        if not used:
            return
        data = self.state.memory.data
        skill_data = data.setdefault("skills", {})
        if not isinstance(skill_data, dict):
            skill_data = {}
        recent = skill_data.get("recently_used", [])
        if not isinstance(recent, list):
            recent = []
        now = datetime.now(timezone.utc).isoformat()
        existing = {
            (item.get("scope"), item.get("name")): item
            for item in recent
            if isinstance(item, dict)
        }
        for skill in used:
            existing[(skill.scope, skill.name)] = {
                "scope": skill.scope,
                "name": skill.name,
                "title": skill.title,
                "description": skill.description,
                "role": role,
                "last_used_at": now,
            }
        skill_data["recently_used"] = sorted(
            existing.values(),
            key=lambda item: item.get("last_used_at", ""),
            reverse=True,
        )[:25]
        data["skills"] = skill_data
        self.state.memory.update(data)
        self.state.memory.persist()

    def dashboard_summary(
        self, *, available_limit: int = 10, recent_limit: int = 5
    ) -> dict[str, list[dict[str, Any]]]:
        available = [
            {
                "scope": skill.scope,
                "name": skill.name,
                "title": skill.title,
                "description": skill.description,
                "path": skill.path,
            }
            for skill in self.manager.list_skills()[:available_limit]
        ]
        skill_data = self.state.memory.data.get("skills", {})
        recent = []
        if isinstance(skill_data, dict):
            recent = [
                item for item in skill_data.get("recently_used", []) if isinstance(item, dict)
            ][:recent_limit]
        return {"available": available, "recently_used": recent}


    def inspect_skills(
        self, *, available_limit: int = 25, recent_limit: int = 10
    ) -> dict[str, Any]:
        """Return UI/COO-friendly detail about approved and recently used skills."""

        summary = self.dashboard_summary(
            available_limit=available_limit, recent_limit=recent_limit
        )
        proposals = self.list_proposals(status="pending")
        return {
            "enabled": self.config.enabled,
            "root": str(self.manager.root),
            "available_count": len(self.manager.list_skills()),
            "recently_used_count": len(summary["recently_used"]),
            "available": summary["available"],
            "recently_used": summary["recently_used"],
            "pending_proposals": [proposal.to_dict() for proposal in proposals[:10]],
        }

    def create_proposal(self, proposal: SkillProposal) -> Path:
        scope_dir = self.proposals_root / proposal.scope
        scope_dir.mkdir(parents=True, exist_ok=True)
        path = scope_dir / f"{proposal.proposal_id}.json"
        path.write_text(json.dumps(proposal.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        self._record_proposal_index(proposal)
        return path

    def list_proposals(self, *, status: str | None = None) -> list[SkillProposal]:
        proposals: list[SkillProposal] = []
        if not self.proposals_root.exists():
            return proposals
        for path in sorted(self.proposals_root.glob("*/*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            proposal = SkillProposal.from_dict(data)
            if status is None or proposal.status == status:
                proposals.append(proposal)
        return proposals

    def approve_proposal(self, proposal_id: str) -> SkillProposal:
        path = self._proposal_path(proposal_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        proposal = SkillProposal.from_dict(data)
        if proposal.status != "pending":
            return proposal
        metadata = {
            "approval_status": "approved",
            "source_tasks": proposal.source_tasks,
            "source_audit_events": proposal.source_audit_events,
            "confidence": proposal.confidence,
            **proposal.metadata,
        }
        if proposal.action == "create":
            self.manager.create_skill(
                scope=proposal.scope,
                name=proposal.name,
                content=proposal.content,
                metadata=metadata,
            )
        else:
            raise ValueError(f"Unsupported proposal action: {proposal.action}")
        proposal.status = "approved"
        path.write_text(json.dumps(proposal.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        self._record_proposal_index(proposal)
        return proposal

    def _proposal_path(self, proposal_id: str) -> Path:
        for path in self.proposals_root.glob(f"*/{proposal_id}.json"):
            return path
        raise FileNotFoundError(f"No skill proposal: {proposal_id}")

    def _record_proposal_index(self, proposal: SkillProposal) -> None:
        data = self.state.memory.data
        proposals = data.setdefault("skill_proposals", [])
        if not isinstance(proposals, list):
            proposals = []
        proposals = [
            p
            for p in proposals
            if not isinstance(p, dict) or p.get("proposal_id") != proposal.proposal_id
        ]
        proposals.append(
            {
                "proposal_id": proposal.proposal_id,
                "scope": proposal.scope,
                "name": proposal.name,
                "status": proposal.status,
                "created_at": proposal.created_at,
                "confidence": proposal.confidence,
            }
        )
        data["skill_proposals"] = proposals[-100:]
        self.state.memory.update(data)
        self.state.memory.persist()

    @staticmethod
    def _task_query(task: CompanyTask) -> str:
        parts = [task.target, task.artifact_type, str(task.payload)]
        if task.context:
            for key in (
                "original_request",
                "prd_summary",
                "design_spec_summary",
                "playbook_guidance",
            ):
                if task.context.get(key):
                    parts.append(str(task.context[key]))
        return "\n".join(parts)


def slugify_skill_name(value: str, *, fallback: str = "learned-workflow") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:70].strip("-")
    return slug if len(slug) >= 2 else fallback
