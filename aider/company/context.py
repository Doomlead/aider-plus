from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from aider.company.skills import CompanySkillManager, SkillLearningConfig
from aider.company.project import Project
from aider.company.recall import RecallEngine
from aider.company.schemas import CompanyTask
from aider.company.state import CompanyStateManager
from aider.memory.explanations import explanation_telemetry, format_recall_explanation
from aider.memory.fabric import MemoryFabric
from aider.memory.pattern_extractor import pattern_text
from aider.memory.retrieval import MemoryRetriever
from aider.memory.store import MemoryStore

# Maximum number of playbook items to inject per category after retrieval.
_MAX_PLAYBOOK_ITEMS = 5
# Maximum number of relevant procedural skills to inject.
_MAX_SKILL_ITEMS = 5
_MAX_RECENT_INJECTED_ITEMS = 5
# Maximum number of PRD lines to inject when truncation is active.
_MAX_PRD_LINES = 80
# Minimum retrieval score to include a chunk. Below this it's noise.
_MIN_SCORE = 0.05
_PREPASS_MAX_ITEMS = 3
_PREPASS_MAX_CHARS = 700


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

        # --- Proactive pre-recall candidate generation (small bounded prepass) ---
        prepass = self._proactive_recall_prepass(task)
        if prepass:
            context["recall_prepass"] = prepass

        # --- Scoped memory recall packet (additive; playbooks/skills unchanged) ---
        context.setdefault("recall_packet", self._build_recall_packet(task))

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
            playbook_explanations = self._playbook_explanations(playbook)
            context["playbook_retrieval_explanations"] = playbook_explanations
            context.setdefault("recall_explanation_telemetry", []).extend(
                self._explanation_telemetry(playbook_explanations)
            )

        # --- Procedural skills (retrieval-filtered) ---
        skills = self._get_relevant_skills(task, requirements)
        if skills:
            manager = CompanySkillManager(self.state, self.skill_learning)
            skill_explanations = self._skill_explanations(skills)
            context["skills"] = [
                self._skill_context_dict(skill, skill_explanations) for skill in skills
            ]
            context["skill_retrieval_explanations"] = skill_explanations
            context.setdefault("recall_explanation_telemetry", []).extend(
                self._explanation_telemetry(skill_explanations)
            )
            context["skill_guidance"] = self._format_skill_guidance_with_explanations(
                manager.format_skill_guidance(skills), skill_explanations
            )
            manager.record_skill_usage(skills, role=task.target)

        return context

    def _build_recall_packet(self, task: CompanyTask) -> dict:
        """Build the scoped memory recall packet injected into department context."""
        return (
            RecallEngine(MemoryStore(self.state.memory))
            .build_recall_packet(task)
            .to_dict()
        )


    def _proactive_recall_prepass(self, task: CompanyTask) -> list[dict[str, Any]]:
        context = task.context if isinstance(task.context, dict) else {}
        payload = task.payload if isinstance(task.payload, dict) else {}
        thread_id = str(context.get("thread_id") or payload.get("thread_id") or "") or None
        channel_id = str(context.get("channel_id") or payload.get("channel_id") or "") or None
        user_id = str(context.get("user_id") or payload.get("user_id") or "") or None
        query = self._task_query(task)[:_PREPASS_MAX_CHARS]
        fabric = MemoryFabric(MemoryStore(self.state.memory))
        return fabric.proactive_recall_prepass(
            query=query,
            thread_id=thread_id,
            channel_id=channel_id,
            user_id=user_id,
            limit=_PREPASS_MAX_ITEMS,
        )

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

    def _get_relevant_skills(
        self, task: CompanyTask, requirements: Iterable[str] | None = None
    ):
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
        scopes = set(manager.scopes_for_role(task.target))
        if wants_shared or want_all:
            scopes.add("shared")
        requested_roles = [
            requirement.split(".", 1)[1]
            for requirement in requirements
            if requirement.startswith("skills.")
            and requirement
            not in {"skills.*", "skills.shared", f"skills.{task.target}"}
        ]
        for role in requested_roles:
            scopes.update(manager.scopes_for_role(role))
        candidates = manager.manager.list_skills(scopes=sorted(scopes))
        allowed = {str(task.target).lower().strip()}
        for requirement in requested_roles:
            allowed.add(str(requirement).lower().strip())
        filtered = []
        for skill in candidates:
            metadata = getattr(skill, "metadata", {}) or {}
            channel_scope = str(metadata.get("channel_scope") or "").lower().strip()
            if not channel_scope:
                filtered.append(skill)
                continue
            participants = set(part for part in channel_scope.split(":") if part)
            if allowed & participants:
                filtered.append(skill)
        return self._rank_skills(
            self._task_query(task), filtered, limit=_MAX_SKILL_ITEMS
        )

    def _requested_skills(self, requirements: list[str], task: CompanyTask):
        return self._get_relevant_skills(task, requirements)

    # ------------------------------------------------------------------
    # Playbook retrieval
    # ------------------------------------------------------------------

    def _requested_playbook(self, requirements: list[str], task: CompanyTask) -> dict:
        """
        Return retrieval-ranked playbook patterns relevant to this task.

        Uses the same playbook corpus as PlaybookManager, then applies the
        explainable hybrid ranker so injected memories are relevant, capped,
        and accompanied by retrieval rationale.
        """
        want_all = "playbook.*" in requirements
        if not want_all and not any(r.startswith("playbook.") for r in requirements):
            return {}

        # Determine which categories were requested.
        if want_all:
            categories = None  # PlaybookManager.query() defaults to all
        else:
            categories = [
                r.split(".", 1)[1] for r in requirements if r.startswith("playbook.")
            ]

        query = self._task_query(task)
        return self._rank_memories(
            query, categories=categories, limit=_MAX_PLAYBOOK_ITEMS
        )

    # ------------------------------------------------------------------
    # Explainable hybrid ranking
    # ------------------------------------------------------------------

    def _rank_memories(
        self,
        query: str,
        *,
        categories: Iterable[str] | None = None,
        limit: int = _MAX_PLAYBOOK_ITEMS,
    ) -> dict[str, list[str]]:
        """Rank playbook memory with relevance, recency, and prior usage signals."""
        playbook = self.state.get_playbook()
        target_cats = list(categories or playbook.keys())
        ranked_by_category: dict[str, list[tuple[str, float, str]]] = {}
        for category in target_cats:
            entries = playbook.get(category, [])
            if not isinstance(entries, list) or not entries:
                continue
            candidates = []
            for index, entry in enumerate(entries):
                text = pattern_text(entry)
                if text:
                    candidates.append((text, entry, index))
            if not candidates:
                continue
            texts = [text for text, _entry, _index in candidates]
            tfidf_scores = {
                text: score for text, score in MemoryRetriever(texts).score(query)
            }
            scored = []
            for text, entry, index in candidates:
                usage_count = self._item_usage_count("playbook", category, text)
                recency_score = self._recency_score(
                    self._item_timestamp(entry)
                    or (str(index) if index == len(candidates) - 1 else "")
                )
                keyword_score = self._keyword_score(query, text)
                score = (
                    tfidf_scores.get(text, 0.0)
                    + (0.35 * keyword_score)
                    + (0.15 * recency_score)
                    + (0.10 * min(usage_count, 5) / 5)
                )
                if score >= _MIN_SCORE or keyword_score > 0:
                    scored.append(
                        (
                            text,
                            score,
                            self._explain_item(
                                "memory", text, query, usage_count, recency_score
                            ),
                        )
                    )
            scored.sort(key=lambda item: item[1], reverse=True)
            if scored:
                ranked_by_category[category] = scored[: max(4, min(6, limit))]
        self._last_playbook_explanations = {
            f"playbook:{category}:{text}": explanation
            for category, items in ranked_by_category.items()
            for text, _score, explanation in items
        }
        return {
            category: [text for text, _score, _explanation in items]
            for category, items in ranked_by_category.items()
        }

    def _rank_skills(
        self, query: str, skills: Iterable[Any], *, limit: int = _MAX_SKILL_ITEMS
    ) -> list[Any]:
        """Rank skills with TF-IDF/name relevance, recency, and usage frequency."""
        skills = list(skills or [])
        if not skills:
            self._last_skill_explanations = {}
            return []
        texts = [skill.injectable_text() for skill in skills]
        tfidf_scores = {
            text: score for text, score in MemoryRetriever(texts).score(query)
        }
        scored = []
        for skill, text in zip(skills, texts):
            usage_count = self._skill_usage_count(skill)
            last_used_at = self._skill_last_used_at(skill)
            recency_score = self._recency_score(last_used_at)
            keyword_score = self._keyword_score(
                query,
                " ".join(
                    [
                        skill.name,
                        skill.name.replace("-", " "),
                        skill.title,
                        skill.description,
                    ]
                ),
            )
            path_score = self._keyword_score(query, skill.name.replace("-", " "))
            score = (
                tfidf_scores.get(text, 0.0)
                + (0.35 * keyword_score)
                + (0.20 * path_score)
                + (0.15 * recency_score)
                + (0.10 * min(usage_count, 5) / 5)
            )
            if score >= _MIN_SCORE and (
                keyword_score > 0
                or path_score > 0
                or tfidf_scores.get(text, 0.0) >= 0.10
            ):
                scored.append(
                    (
                        skill,
                        score,
                        self._explain_item(
                            "skill", text, query, usage_count, recency_score
                        ),
                    )
                )
        scored.sort(key=lambda item: (-item[1], item[0].scope, item[0].name))
        selected = scored[: max(4, min(6, limit))]
        self._last_skill_explanations = {
            f"{skill.scope}/{skill.name}": explanation
            for skill, _score, explanation in selected
        }
        return [skill for skill, _score, _explanation in selected]

    def _playbook_explanations(self, playbook: dict) -> list[str]:
        explanations = getattr(self, "_last_playbook_explanations", {})
        items = []
        for category, entries in playbook.items():
            for text in entries:
                key = f"playbook:{category}:{text}"
                explanation = explanations.get(key) or self._explain_item(
                    "memory", str(text), "", 0, 0.0
                )
                items.append(f"{category} — {explanation}")
        self._record_recently_injected("playbook", items)
        return items

    def _skill_explanations(self, skills: Iterable[Any]) -> list[str]:
        explanations = getattr(self, "_last_skill_explanations", {})
        items = []
        for skill in skills:
            label = f"{skill.scope}/{skill.name}"
            explanation = explanations.get(label) or self._explain_item(
                "skill", skill.injectable_text(), "", 0, 0.0
            )
            items.append(f"{label} — {explanation}")
        self._record_recently_injected("skill", items)
        return items

    def _record_recently_injected(
        self, item_type: str, explanations: list[str]
    ) -> None:
        if not explanations:
            return
        data = self.state.memory.data
        knowledge = data.setdefault("knowledge", {})
        recent = knowledge.get("recently_injected", [])
        if not isinstance(recent, list):
            recent = []
        now = datetime.now(timezone.utc).isoformat()
        for explanation in explanations:
            recent.insert(
                0, {"type": item_type, "explanation": explanation, "injected_at": now}
            )
        knowledge["recently_injected"] = recent[:_MAX_RECENT_INJECTED_ITEMS]
        data["knowledge"] = knowledge
        self.state.memory.update(data)
        self.state.memory.persist()

    def _skill_context_dict(self, skill: Any, explanations: list[str]) -> dict:
        data = dict(skill.__dict__)
        label = f"{skill.scope}/{skill.name}"
        for explanation in explanations:
            if explanation.startswith(f"{label} — "):
                data["retrieval_explanation"] = explanation
                break
        return data

    @staticmethod
    def _format_skill_guidance_with_explanations(
        guidance: list[str], explanations: list[str]
    ) -> list[str]:
        by_label = {
            item.split(" — ", 1)[0]: item.split(" — ", 1)[1]
            for item in explanations
            if " — " in item
        }
        formatted = []
        for item in guidance:
            label = item.split(":", 1)[0]
            why = by_label.get(label)
            formatted.append(f"{item} — Why included: {why}" if why else item)
        return formatted

    def _item_usage_count(self, item_type: str, category: str, text: str) -> int:
        memory = getattr(self.state, "memory", None)
        memory_data = getattr(memory, "data", {})
        recent = memory_data.get("knowledge", {}).get("recently_injected", [])
        if not isinstance(recent, list):
            return 0
        needle = f"{category} —" if item_type == "playbook" else text
        return sum(
            1
            for item in recent
            if isinstance(item, dict) and needle in str(item.get("explanation", ""))
        )

    def _skill_usage_record(self, skill: Any) -> dict:
        recent = self.state.memory.data.get("skills", {}).get("recently_used", [])
        if not isinstance(recent, list):
            return {}
        for item in recent:
            if (
                isinstance(item, dict)
                and item.get("scope") == skill.scope
                and item.get("name") == skill.name
            ):
                return item
        return {}

    def _skill_usage_count(self, skill: Any) -> int:
        return int(self._skill_usage_record(skill).get("usage_count") or 0)

    def _skill_last_used_at(self, skill: Any) -> str:
        return str(self._skill_usage_record(skill).get("last_used_at") or "")

    @staticmethod
    def _item_timestamp(item: Any) -> str:
        if isinstance(item, dict):
            return str(
                item.get("last_used_at")
                or item.get("updated_at")
                or item.get("created_at")
                or item.get("timestamp")
                or ""
            )
        return ""

    @staticmethod
    def _recency_score(timestamp: str) -> float:
        if not timestamp:
            return 0.0
        if timestamp.isdigit():
            return 0.05
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
        return 1.0 / (1.0 + age_days)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        stop = {
            "a",
            "an",
            "and",
            "are",
            "for",
            "in",
            "of",
            "on",
            "or",
            "the",
            "to",
            "with",
        }
        return {
            tok
            for tok in re.split(r"[^a-z0-9]+", str(text).lower())
            if tok and tok not in stop
        }

    @classmethod
    def _keyword_score(cls, query: str, text: str) -> float:
        q = cls._tokens(query)
        if not q:
            return 0.0
        return len(q & cls._tokens(text)) / len(q)

    @classmethod
    def _matching_terms(cls, query: str, text: str, *, limit: int = 3) -> list[str]:
        return sorted(cls._tokens(query) & cls._tokens(text))[:limit]

    @classmethod
    def _explain_item(
        cls,
        item_type: str,
        text: str,
        query: str,
        usage_count: int,
        recency_score: float,
    ) -> str:
        terms = cls._matching_terms(query, text)
        scope_reason = "procedural memory" if item_type == "memory" else "procedural skill"
        freshness = "fresh" if recency_score >= 0.25 else "stale"
        evidence_count = max(1, usage_count)
        return format_recall_explanation(
            label=f"{item_type}",
            matching_terms=terms,
            scope_reason=scope_reason,
            confidence=max(0.0, min(1.0, 0.35 + recency_score)),
            updated_at=freshness,
            evidence_count=evidence_count,
        )

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


    @staticmethod
    def _explanation_telemetry(explanations: list[str]) -> list[dict[str, Any]]:
        telemetry: list[dict[str, Any]] = []
        for item in explanations:
            label, payload = (item.split(" — ", 1) + [""])[:2] if " — " in item else ("", item)
            telemetry.append(
                explanation_telemetry(
                    label=label or "retrieval",
                    matching_terms=[],
                    scope_reason="context_injection",
                    confidence=0.5,
                    updated_at="",
                    evidence_count=1,
                )
                | {"explanation": payload or item}
            )
        return telemetry
