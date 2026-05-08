from __future__ import annotations

from typing import Iterable, Optional

from aider.company.project import Project
from aider.company.schemas import CompanyTask
from aider.company.state import CompanyStateManager
from aider.memory.retrieval import MemoryRetriever

# Maximum number of playbook items to inject per category after retrieval.
_MAX_PLAYBOOK_ITEMS = 5
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

    def __init__(self, state: CompanyStateManager):
        self.state = state

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
    # Playbook retrieval
    # ------------------------------------------------------------------

    def _requested_playbook(self, requirements: list[str], task: CompanyTask) -> dict:
        """
        Return a retrieval-filtered subset of playbook entries.

        For each playbook category requested, score its entries against the
        task query and keep only the top _MAX_PLAYBOOK_ITEMS. If a category
        has fewer than that it's included in full.
        """
        want_all = "playbook.*" in requirements
        if not want_all and not any(r.startswith("playbook.") for r in requirements):
            return {}

        playbook = self.state.get_playbook()
        query = self._task_query(task)

        result: dict[str, list[str]] = {}

        for key, values in playbook.items():
            if not isinstance(values, list) or not values:
                continue

            # Check if this key was requested.
            if not want_all and f"playbook.{key}" not in requirements:
                continue

            str_values = [str(v) for v in values if v]
            if len(str_values) <= _MAX_PLAYBOOK_ITEMS:
                # Small enough — no retrieval needed.
                result[key] = str_values
                continue

            # Score and filter.
            top = self.retrieve(
                query, str_values, k=_MAX_PLAYBOOK_ITEMS, min_score=_MIN_SCORE
            )
            if top:
                # Preserve original list order among winners.
                top_texts = {chunk for chunk, _ in top}
                result[key] = [v for v in str_values if v in top_texts]
            else:
                # Nothing scored — take the most recent items as a safe fallback.
                result[key] = str_values[-_MAX_PLAYBOOK_ITEMS:]

        return result

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
