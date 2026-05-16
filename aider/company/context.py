from __future__ import annotations

from typing import Iterable, Optional

from aider.company.playbook import PlaybookManager
from aider.company.skills import CompanySkillManager, SkillLearningConfig
from aider.company.project import Project
from aider.company.schemas import CompanyTask
from aider.company.state import CompanyStateManager
from aider.memory.retrieval import MemoryRetriever

# Maximum number of playbook items to inject per category after retrieval.
_MAX_PLAYBOOK_ITEMS = 5
# Maximum number of relevant procedural skills to inject.
_MAX_SKILL_ITEMS = 5
# Maximum number of PRD lines to inject when truncation is active.
_MAX_PRD_LINES = 80
# Minimum retrieval score to include a chunk. Below this it's noise.
_MIN_SCORE = 0.05


class ContextBuilder:
    """
    Build department task context from declared requirements and project state.

    Instead of injecting all playbook entries and the full PRD string, this
    builder scores available memory chunks against the task's content using
    TF-IDF cosine similarity and injects only the most relevant material.

    This keeps context small as project memory grows across sessions.
    """

    def __init__(
        self,
        state: CompanyStateManager,
        skill_learning: SkillLearningConfig | None = None,
    ):
        self.state = state
        self.skill_learning = skill_learning or SkillLearningConfig()

    def build(
        self,
        task: CompanyTask,
        requirements: Iterable[str],
        project: Optional[Project] = None,
    ) -> dict:
        context = dict(task.context or {})
        requirements = list(requirements or [])

        # --- Fixed project fields (no retrieval needed, always cheap) ---
        if project is not None:
            if "project.name" in requirements:
                context.setdefault("project_name", project.name)
            if "project.phase" in requirements:
                context.setdefault("project_phase", project.phase)
            if "project.prd" in requirements and project.prd:
                context.setdefault(
                    "prd_content",
                    self._retrieve_prd(task, project.prd),
                )
            if "project.design_spec" in requirements and project.design_spec:
                context.setdefault("design_spec", project.design_spec)

        # --- Playbook (retrieval-filtered) ---
        playbook = self._requested_playbook(requirements, task)
        if playbook:
            context["playbook"] = playbook
            context["playbook_guidance"] = self._format_playbook_guidance(playbook)

        # --- Procedural skills (retrieval-filtered) ---
        skills = self._get_relevant_skills(task, requirements)
        if skills:
            manager = CompanySkillManager(self.state, self.skill_learning)
            context["skills"] = [skill.__dict__ for skill in skills]
            context["skill_guidance"] = manager.format_skill_guidance(skills)
            manager.record_skill_usage(skills, role=task.target)

        return context

    def retrieve(
        self,
        query: str,
        chunks: Iterable[str],
        *,
        k: int = 5,
        min_score: float = _MIN_SCORE,
    ) -> list[tuple[str, float]]:
        """Score memory chunks against a query and return the top matches."""
        return MemoryRetriever(chunks).top_k(query, k=k, min_score=min_score)

    # ------------------------------------------------------------------
    # PRD retrieval
    # ------------------------------------------------------------------

    def _retrieve_prd(self, task: CompanyTask, prd: str) -> str:
        """
        Return a relevance-filtered slice of the PRD.

        Strategy:
        - Split PRD into paragraph-level chunks (double newline boundaries).
        - Score each chunk against the task query.
        - Return the top-scoring chunks up to _MAX_PRD_LINES lines total,
          preserving their original document order.
        - If the PRD is short enough already, return it verbatim.
        """
        lines = prd.splitlines()
        if len(lines) <= _MAX_PRD_LINES:
            # Short PRD — inject whole thing, no truncation needed.
            return prd

        paragraphs = [p.strip() for p in prd.split("\n\n") if p.strip()]
        if len(paragraphs) <= 1:
            # No paragraph boundaries — fall back to line truncation.
            return "\n".join(lines[:_MAX_PRD_LINES])

        query = self._task_query(task)
        top = self.retrieve(query, paragraphs, k=10, min_score=_MIN_SCORE)

        if not top:
            # Nothing scored above threshold — return first N lines as safe fallback.
            return "\n".join(lines[:_MAX_PRD_LINES])

        # Reconstruct in document order (preserve narrative flow).
        top_texts = {chunk for chunk, _ in top}
        ordered = [p for p in paragraphs if p in top_texts]

        # Cap total line count.
        result_lines: list[str] = []
        for paragraph in ordered:
            para_lines = paragraph.splitlines()
            if len(result_lines) + len(para_lines) > _MAX_PRD_LINES:
                remaining = _MAX_PRD_LINES - len(result_lines)
                result_lines.extend(para_lines[:remaining])
                break
            result_lines.extend(para_lines)
            result_lines.append("")  # blank line between paragraphs

        return "\n".join(result_lines).strip()

    # ------------------------------------------------------------------
    # Procedural skill retrieval
    # ------------------------------------------------------------------

    def _get_relevant_skills(self, task: CompanyTask, requirements: Iterable[str] | None = None):
        """Return the top shared + role-specific skills for task prompt injection.

        Skills are intentionally injected as short summaries instead of full
        documents; agents can consult the named skill when the summary matches
        the current work.
        """
        requirements = list(requirements or ["skills.*"])
        want_all = "skills.*" in requirements
        wants_role = f"skills.{task.target}" in requirements
        wants_shared = "skills.shared" in requirements
        if not (
            want_all
            or wants_role
            or wants_shared
            or any(r.startswith("skills.") for r in requirements)
        ):
            return []
        if not self.skill_learning.enabled:
            return []
        manager = CompanySkillManager(self.state, self.skill_learning)
        original_k = manager.config.query_k
        manager.config.query_k = min(max(original_k, 3), _MAX_SKILL_ITEMS)
        try:
            skills = list(manager.query_for_task(task, role=task.target))
            requested_roles = [
                requirement.split(".", 1)[1]
                for requirement in requirements
                if requirement.startswith("skills.")
                and requirement not in {"skills.*", "skills.shared", f"skills.{task.target}"}
            ]
            seen = {(skill.scope, skill.name) for skill in skills}
            for role in requested_roles:
                for skill in manager.query_for_task(task, role=role):
                    if (skill.scope, skill.name) not in seen:
                        skills.append(skill)
                        seen.add((skill.scope, skill.name))
            return skills
        finally:
            manager.config.query_k = original_k

    def _requested_skills(self, requirements: list[str], task: CompanyTask):
        return self._get_relevant_skills(task, requirements)

    # ------------------------------------------------------------------
    # Playbook retrieval
    # ------------------------------------------------------------------

    def _requested_playbook(self, requirements: list[str], task: CompanyTask) -> dict:
        """
        Return retrieval-ranked playbook patterns relevant to this task.

        Delegates to PlaybookManager.query() which:
        - Handles both legacy string entries and structured pattern dicts
        - Deduplicates at write time so the corpus is already clean
        - Scores against the task query using MemoryRetriever
        - Returns ordered by relevance, capped at _MAX_PLAYBOOK_ITEMS
        """
        want_all = "playbook.*" in requirements
        if not want_all and not any(r.startswith("playbook.") for r in requirements):
            return {}

        # Determine which categories were requested.
        if want_all:
            categories = None  # PlaybookManager.query() defaults to all
        else:
            categories = [r.split(".", 1)[1] for r in requirements if r.startswith("playbook.")]

        query = self._task_query(task)
        manager = PlaybookManager(self.state)
        ranked = manager.query(
            query,
            categories=categories,
            k=_MAX_PLAYBOOK_ITEMS,
            min_score=_MIN_SCORE,
        )

        # ranked is already {category: [text, ...]} — pass through to formatting.
        return ranked

    # ------------------------------------------------------------------
    # Query construction
    # ------------------------------------------------------------------

    @staticmethod
    def _task_query(task: CompanyTask) -> str:
        """
        Build a retrieval query string from the task's most informative fields.

        Priority: explicit string payload > prd_content > original_request >
        task_id. We never embed the entire payload dict — just the parts
        that describe *what* is being worked on.
        """
        if isinstance(task.payload, str):
            return task.payload[:2000]

        if isinstance(task.payload, dict):
            parts = []
            for key in (
                "original_request",
                "prd_content",
                "instruction",
                "qa_report",
                "description",
            ):
                val = task.payload.get(key)
                if val and isinstance(val, str):
                    parts.append(val[:500])
            if parts:
                return " ".join(parts)[:2000]

        # Last resort: task_id gives at least some signal.
        return task.task_id

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_playbook_guidance(playbook: dict) -> list[str]:
        guidance = []
        for entries in playbook.values():
            for entry in entries:
                guidance.append(str(entry))
        return guidance
