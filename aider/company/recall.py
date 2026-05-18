from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from aider.company.schemas import CompanyTask
from aider.memory.records import MemoryQuery, MemoryRecord
from aider.memory.retrieval import MemoryRetriever
from aider.memory.store import MemoryStore

_MAX_RECALL_ITEMS = 5
_MIN_RECALL_SCORE = 0.05


@dataclass
class RecallPacket:
    """Scoped, explainable memory bundle injected into department context."""

    thread: list[dict[str, Any]] = field(default_factory=list)
    department_private: list[dict[str, Any]] = field(default_factory=list)
    channel: list[dict[str, Any]] = field(default_factory=list)
    project: list[dict[str, Any]] = field(default_factory=list)
    user: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    why_included: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RecallEngine:
    """Build department-scoped recall packets from the local memory fabric."""

    def __init__(self, store: MemoryStore, *, limit_per_scope: int = _MAX_RECALL_ITEMS):
        self.store = store
        self.limit_per_scope = limit_per_scope
        self.skill_state = (
            store.project_memory.data.get("skills", {})
            if hasattr(store, "project_memory")
            else {}
        )

    def build_recall_packet(self, task: CompanyTask) -> RecallPacket:
        """Return visible, ranked memory grouped by recall scope for *task*."""
        context = (
            task.context if isinstance(getattr(task, "context", None), dict) else {}
        )
        query_text = self._task_query(task)
        department_scope = f"department:{task.target}"
        role_scope = f"role:{task.target}"

        thread_id = self._first_value(context, task.payload, "thread_id", "session_id")
        channel_id = self._first_value(context, task.payload, "channel_id", "channel")
        user_id = self._first_value(context, task.payload, "user_id", "user", "author")

        packet = RecallPacket()
        packet.thread = (
            self._recall_scope(
                scope=f"thread:{thread_id}",
                requester_scope=f"thread:{thread_id}",
                query_text=query_text,
                category="thread",
            )
            if thread_id
            else []
        )
        related_channel_scopes = self._department_channel_scopes(str(task.target))
        packet.department_private = self._recall_many_scopes(
            scopes=(department_scope, role_scope, *related_channel_scopes),
            requester_scope=department_scope,
            query_text=query_text,
            category="department_private",
        )
        packet.channel = (
            self._recall_scope(
                scope=f"channel:{channel_id}",
                requester_scope=f"channel:{channel_id}",
                query_text=query_text,
                category="channel",
            )
            if channel_id
            else []
        )
        packet.project = self._recall_scope(
            scope="project",
            requester_scope=department_scope,
            query_text=query_text,
            category="project",
        )
        packet.user = (
            self._recall_scope(
                scope=f"user:{user_id}",
                requester_scope=f"user:{user_id}",
                requester=str(user_id),
                query_text=query_text,
                category="user",
            )
            if user_id
            else []
        )
        packet.skills = self._recall_scope(
            scope=f"skill:{task.target}",
            requester_scope=f"skill:{task.target}",
            query_text=query_text,
            category="skills",
        )

        packet.why_included = {
            str(item.get("id") or item.get("record_id")): str(item["why_included"])
            for items in (
                packet.thread,
                packet.department_private,
                packet.channel,
                packet.project,
                packet.user,
                packet.skills,
            )
            for item in items
            if item.get("id") or item.get("record_id")
        }
        return packet


    def _department_channel_scopes(self, department: str) -> list[str]:
        dept = str(department or "").lower().strip()
        if not dept:
            return []
        scopes: set[str] = set()
        for record in self.store.query_records():
            scope = str(record.scope or "")
            if scope.startswith("channel_pair:"):
                parts = [p for p in scope.split(":")[1:] if p]
                if dept in parts:
                    scopes.add(scope)
            elif scope.startswith("channel:"):
                # Legacy department-pair channel scopes were encoded as
                # channel:<department_a>:<department_b>. Keep reading them, but
                # prefer channel_pair: for new department-to-department memory.
                parts = [p for p in scope.split(":")[1:] if p]
                if dept in parts:
                    scopes.add(scope)
            else:
                continue
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            cid = str(metadata.get("channel_id") or metadata.get("channel") or "").lower().strip()
            if cid and dept in cid.split(":"):
                scopes.add(f"channel:{cid}")
        return sorted(scopes)

    def _recall_many_scopes(
        self,
        *,
        scopes: Iterable[str],
        requester_scope: str,
        query_text: str,
        category: str,
    ) -> list[dict[str, Any]]:
        records: list[MemoryRecord] = []
        seen: set[str] = set()
        for scope in scopes:
            for record in self._visible_exact_scope(
                scope, requester_scope=requester_scope
            ):
                if record.id not in seen:
                    seen.add(record.id)
                    records.append(record)
        return self._rank_records(records, query_text=query_text, category=category)

    def _recall_scope(
        self,
        *,
        scope: str,
        requester_scope: str,
        query_text: str,
        category: str,
        requester: str | None = None,
    ) -> list[dict[str, Any]]:
        records = self._visible_exact_scope(
            scope, requester_scope=requester_scope, requester=requester
        )
        return self._rank_records(records, query_text=query_text, category=category)

    def _visible_exact_scope(
        self, scope: str, *, requester_scope: str, requester: str | None = None
    ) -> list[MemoryRecord]:
        records = self.store.query_records(
            MemoryQuery(
                scope=scope, requester_scope=requester_scope, requester=requester
            )
        )
        if scope in {"project", "shared", "global"}:
            return [record for record in records if record.scope == scope]
        return [
            record
            for record in records
            if record.scope == scope or record.scope.startswith(f"{scope}:")
        ]

    def _rank_records(
        self,
        records: list[MemoryRecord],
        *,
        query_text: str,
        category: str,
    ) -> list[dict[str, Any]]:
        if not records:
            return []
        texts = [self._record_text(record) for record in records]
        tfidf_scores = {
            text: score for text, score in MemoryRetriever(texts).score(query_text)
        }
        scored: list[tuple[MemoryRecord, float, str]] = []
        for record, text in zip(records, texts):
            keyword_terms = self._matching_terms(query_text, text)
            score = tfidf_scores.get(text, 0.0) + (0.20 * len(keyword_terms))
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            reinforcement = int(metadata.get("reinforcement_count") or 0)
            signal = int(metadata.get("reinforcement_signal") or 0)
            score += min(0.6, reinforcement * 0.05)
            score += max(-0.4, min(0.4, signal * 0.04))
            adjustment = self._skill_adjustment(record)
            if adjustment <= -9.0:
                continue
            score += adjustment
            if score >= _MIN_RECALL_SCORE or keyword_terms or not query_text:
                scored.append(
                    (
                        record,
                        score,
                        self._explain(record, category, keyword_terms, score),
                    )
                )
        scored.sort(
            key=lambda item: (-item[1], item[0].updated_at or item[0].created_at)
        )
        return [
            self._record_payload(record, why)
            for record, _score, why in scored[: self.limit_per_scope]
        ]

    @staticmethod
    def _record_payload(record: MemoryRecord, why: str) -> dict[str, Any]:
        payload = record.to_dict()
        payload["why_included"] = why
        return payload

    @staticmethod
    def _record_text(record: MemoryRecord) -> str:
        return " ".join(
            str(part)
            for part in (record.kind, record.content, record.tags, record.metadata)
            if part
        )[:2000]

    @staticmethod
    def _matching_terms(query: str, text: str) -> list[str]:
        query_terms = RecallEngine._tokens(query)
        if not query_terms:
            return []
        text_terms = RecallEngine._tokens(text)
        return sorted(query_terms & text_terms)[:3]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        import re

        stop = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with"}
        return {
            token
            for token in re.split(r"[^a-z0-9]+", str(text).lower())
            if token and token not in stop
        }

    @staticmethod
    def _explain(
        record: MemoryRecord, category: str, matching_terms: list[str], score: float
    ) -> str:
        scope_reason = category.replace("_", " ")
        if matching_terms:
            match_reason = f"matched task keyword(s): {', '.join(matching_terms)}"
        else:
            match_reason = "ranked as relevant visible memory"
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        reinforcement = int(metadata.get("reinforcement_count") or 0)
        signal = int(metadata.get("reinforcement_signal") or 0)
        reinforcement_summary = (
            f" Reinforcement: count={reinforcement}, signal={signal}."
            if reinforcement or signal
            else ""
        )
        return (
            f"Included from {scope_reason} scope because it {match_reason}."
            f"{reinforcement_summary}"
        )

    def _skill_adjustment(self, record: MemoryRecord) -> float:
        skills = self.skill_state if isinstance(self.skill_state, dict) else {}
        archived = skills.get("archived", {}) if isinstance(skills, dict) else {}
        if not isinstance(archived, dict):
            archived = {}
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        skill_id = str(metadata.get("skill_id") or "").strip()
        if skill_id and isinstance(archived.get(skill_id), dict):
            return -10.0
        proposals = self.store.project_memory.data.get("skill_proposals", [])
        if skill_id and isinstance(proposals, list):
            for p in proposals:
                if (
                    isinstance(p, dict)
                    and p.get("status") == "pending"
                    and p.get("action") == "patch"
                    and p.get("target_skill_id") == skill_id
                ):
                    return -0.25
        return 0.0

    @staticmethod
    def _first_value(context: dict, payload: Any, *keys: str) -> str | None:
        payload_dict = payload if isinstance(payload, dict) else {}
        for source in (context, payload_dict):
            for key in keys:
                value = source.get(key) if isinstance(source, dict) else None
                if value:
                    return str(value)
        return None

    @staticmethod
    def _task_query(task: CompanyTask) -> str:
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
                "question",
            ):
                value = task.payload.get(key)
                if isinstance(value, str) and value:
                    parts.append(value[:500])
            if parts:
                return " ".join(parts)[:2000]
        return task.task_id
