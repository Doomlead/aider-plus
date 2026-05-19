from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aider.company.skills import CompanySkillManager, SkillLearningConfig, SkillProposal
from aider.company.state import CompanyStateManager
from aider.memory.store import MemoryStore


class KnowledgeManager:
    """Unified read/approval surface for repo-local institutional knowledge."""

    def __init__(
        self,
        state: CompanyStateManager,
        skill_config: SkillLearningConfig | None = None,
    ):
        self.state = state
        self.memory = state.memory
        self.skill_manager = CompanySkillManager(state, skill_config)
        self.repo_path = Path(self.memory.repo_path)

    def get_all_playbooks(self) -> list[dict[str, Any]]:
        playbook = self.state.get_playbook()
        items: list[dict[str, Any]] = []
        for category, value in sorted(playbook.items()):
            if isinstance(value, list):
                for index, entry in enumerate(value):
                    items.append(self._playbook_item(category, entry, index))
            elif isinstance(value, dict):
                for key, entry in sorted(value.items()):
                    item = self._playbook_item(category, entry, str(key))
                    item["key"] = str(key)
                    items.append(item)
            elif value not in (None, ""):
                items.append(self._playbook_item(category, value, 0))
        return items

    def get_all_skills(self) -> list[dict[str, Any]]:
        return [
            self._skill_to_dict(skill)
            for skill in self.skill_manager.manager.list_skills()
        ]

    def get_recent_skills(self, *, limit: int = 25) -> list[dict[str, Any]]:
        skill_data = self.memory.data.get("skills", {})
        if not isinstance(skill_data, dict):
            return []
        recent = [
            item
            for item in skill_data.get("recently_used", [])
            if isinstance(item, dict)
        ]
        normalized = []
        for item in sorted(
            recent, key=lambda item: item.get("last_used_at", ""), reverse=True
        )[:limit]:
            payload = dict(item)
            payload.setdefault("type", "recent_skill")
            payload.setdefault(
                "id", f"recent-skill:{payload.get('scope')}:{payload.get('name')}"
            )
            payload.setdefault(
                "summary", payload.get("description") or payload.get("title") or ""
            )
            normalized.append(payload)
        return normalized

    def get_recently_injected(self, *, limit: int = 5) -> list[dict[str, Any]]:
        knowledge = self.memory.data.get("knowledge", {})
        if not isinstance(knowledge, dict):
            return []
        recent = knowledge.get("recently_injected", [])
        if not isinstance(recent, list):
            return []
        items = [dict(item) for item in recent if isinstance(item, dict)]
        return sorted(
            items, key=lambda item: item.get("injected_at", ""), reverse=True
        )[:limit]


    def get_skills_needing_patch(self) -> list[dict[str, Any]]:
        return [
            proposal
            for proposal in self.get_skill_proposals(status="pending")
            if proposal.get("action") == "patch"
        ]

    def get_skills_needing_retirement(self) -> list[dict[str, Any]]:
        return [
            proposal
            for proposal in self.get_skill_proposals(status="pending")
            if proposal.get("action") == "retire"
        ]

    def get_evidence_for_skill(self, skill_id: str) -> dict[str, Any]:
        skill = next((s for s in self.get_all_skills() if s.get("id") == skill_id), None)
        if skill:
            meta = skill.get("metadata") if isinstance(skill.get("metadata"), dict) else {}
            return {
                "skill": skill,
                "source_memory_records": meta.get("source_memory_records") or [],
                "source_tasks": meta.get("source_tasks") or [],
                "outcome_summary": meta.get("outcome_summary") or "",
                "recent_injections": [
                    item for item in self.get_recently_injected(limit=25)
                    if skill.get("name") and skill.get("name") in json.dumps(item, default=str)
                ][:5],
            }
        proposal = next((p for p in self.get_skill_proposals() if p.get("proposal_id") == skill_id), None)
        if proposal:
            return {"proposal": proposal, "source_memory_records": proposal.get("source_memory_records") or [], "source_tasks": proposal.get("source_tasks") or []}
        return {}

    def get_memory_record(self, record_id: str) -> dict[str, Any] | None:
        store = MemoryStore(self.memory)
        record = store.get_record(record_id)
        if record is not None:
            self._record_reader_metric(canonical_only=True)
            return record.to_dict()
        if self._legacy_fallback_enabled():
            records = ((self.memory.data.get("memory") or {}).get("records") or [])
            for legacy_record in records:
                if isinstance(legacy_record, dict) and legacy_record.get("id") == record_id:
                    self._record_reader_metric(canonical_only=False)
                    return dict(legacy_record)
        self._record_reader_metric(canonical_only=True)
        return None

    @staticmethod
    def _legacy_fallback_enabled() -> bool:
        return str(os.getenv("AIDER_MEMORY_CANONICAL_READER_FALLBACK", "")).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _record_reader_metric(self, *, canonical_only: bool) -> None:
        observability = self.memory.data.setdefault("observability", {})
        if not isinstance(observability, dict):
            return
        memory_metrics = observability.setdefault("memory_metrics", {})
        if not isinstance(memory_metrics, dict):
            return
        total = int(memory_metrics.get("reader_requests_total") or 0) + 1
        canonical = int(memory_metrics.get("reader_requests_canonical_only") or 0)
        if canonical_only:
            canonical += 1
        memory_metrics["reader_requests_total"] = total
        memory_metrics["reader_requests_canonical_only"] = canonical
        memory_metrics["reader_requests_canonical_only_pct"] = round(
            (canonical / total) * 100.0, 2
        )
        self.memory.update({"observability": observability})
        self.memory.persist()

    def explain_retrieval(self, query: str, context_items) -> list[str]:
        """Explain why memories or skills were selected for a query."""
        explanations: list[str] = []
        if isinstance(context_items, dict):
            candidates = []
            for value in context_items.values():
                candidates.extend(value if isinstance(value, list) else [value])
        else:
            candidates = list(context_items or [])
        terms = {
            term for term in str(query or "").lower().replace("-", " ").split() if term
        }
        for item in candidates:
            if isinstance(item, str) and "Why this was included:" in item:
                explanations.append(item)
                continue
            if isinstance(item, dict) and item.get("retrieval_explanation"):
                explanations.append(str(item["retrieval_explanation"]))
                continue
            text = (
                json.dumps(item, default=str, ensure_ascii=False)
                if isinstance(item, dict)
                else str(item)
            )
            matches = sorted(term for term in terms if term and term in text.lower())[
                :3
            ]
            label = self._item_label(item)
            if matches:
                reason = (
                    f"{label} — Why this was included: "
                    f"matches keyword(s) {', '.join(matches)}."
                )
            else:
                reason = (
                    f"{label} — Why this was included: it was selected as one of "
                    "the strongest available knowledge matches."
                )
            explanations.append(reason)
        return explanations

    def get_skill_proposals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        proposals: list[SkillProposal] = []
        for proposal_status in ([status] if status else ["pending", "approved"]):
            if proposal_status:
                proposals.extend(
                    self.skill_manager.list_proposals(status=proposal_status)
                )
        if status is None:
            known = {
                (proposal.proposal_id, proposal.status): proposal
                for proposal in proposals
            }
            proposals = list(known.values())
        items = []
        for proposal in sorted(proposals, key=lambda p: p.created_at, reverse=True):
            payload = proposal.to_dict()
            payload.setdefault("type", "skill_proposal")
            payload.setdefault("id", f"skill-proposal:{proposal.proposal_id}")
            payload.setdefault("summary", proposal.rationale or proposal.title)
            items.append(payload)
        return items

    def get_coo_memory_summary(self, *, limit: int = 50) -> dict[str, Any]:
        entries = self._read_coo_memory(limit=limit)
        profile = self._read_coo_profile()
        return {
            "profile": profile,
            "entries": entries,
            "entry_count": len(entries),
            "latest_timestamp": entries[-1].get("created_at") if entries else None,
        }

    def get_overview(self, *, query: str = "") -> dict[str, Any]:
        playbooks = self.get_all_playbooks()
        skills = self.get_all_skills()
        recent_skills = self.get_recent_skills()
        recently_injected = self.get_recently_injected()
        proposals = self.get_skill_proposals()
        needing_patch = self.get_skills_needing_patch()
        needing_retirement = self.get_skills_needing_retirement()
        coo_memory = self.get_coo_memory_summary()
        skill_snapshot = self.skill_manager.inspect_skills()
        retired_archive = skill_snapshot.get("retired_skills_archive", [])
        search_results = self.search_knowledge(query) if query else []
        memory_health = MemoryStore(self.memory).get_metrics()
        return {
            "playbooks": playbooks,
            "skills": skills,
            "recent_skills": recent_skills,
            "recently_injected": [
                {
                    **item,
                    "why_included": item.get("explanation") or item.get("retrieval_explanation") or "Included by recall ranking.",
                }
                for item in recently_injected
            ],
            "proposals": proposals,
            "pending_proposals": [p for p in proposals if p.get("status") == "pending"],
            "approved_proposals": [
                p for p in proposals if p.get("status") == "approved"
            ],
            "skills_needing_patch": needing_patch,
            "skills_needing_retirement": needing_retirement,
            "retired_skills_archive": retired_archive,
            "coo_memory": coo_memory,
            "search_results": search_results,
            "memory_health": memory_health,
            "counts": {
                "playbooks": len(playbooks),
                "skills": len(skills),
                "recent_skills": len(recent_skills),
                "recently_injected": len(recently_injected),
                "proposals": len(proposals),
                "pending_proposals": len(
                    [p for p in proposals if p.get("status") == "pending"]
                ),
                "coo_memory_entries": coo_memory.get("entry_count", 0),
                "skills_needing_patch": len(needing_patch),
                "skills_needing_retirement": len(needing_retirement),
                "retired_skills_archive": len(retired_archive),
            },
        }

    def search_knowledge(self, query: str) -> list[dict[str, Any]]:
        terms = [term for term in str(query or "").lower().split() if term]
        if not terms:
            return []
        candidates: list[dict[str, Any]] = []
        candidates.extend(self.get_all_playbooks())
        candidates.extend(self.get_all_skills())
        candidates.extend(self.get_recent_skills())
        candidates.extend(self.get_recently_injected())
        candidates.extend(self.get_skill_proposals())
        candidates.extend(self.get_coo_memory_summary().get("entries", []))

        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in candidates:
            haystack = json.dumps(item, default=str, ensure_ascii=False).lower()
            score = sum(1 for term in terms if term in haystack)
            if score <= 0:
                continue
            key = (
                str(item.get("type") or item.get("status") or "knowledge"),
                str(
                    item.get("id")
                    or item.get("proposal_id")
                    or item.get("path")
                    or item.get("name")
                    or haystack[:80]
                ),
            )
            if key in seen:
                continue
            seen.add(key)
            result = dict(item)
            result["match_score"] = score
            result.setdefault("lifecycle_status", result.get("status") or ("approved" if result.get("type") == "skill" else "active"))
            if result.get("type") == "skill_proposal":
                result["evidence_summary"] = ", ".join((result.get("source_memory_records") or [])[:3])
            results.append(result)
        return sorted(
            results,
            key=lambda item: (
                -int(item.get("match_score", 0)),
                str(item.get("title") or item.get("name") or ""),
            ),
        )

    def approve_skill_proposal(self, proposal_id: str) -> dict[str, Any]:
        return self.skill_manager.approve_proposal(proposal_id).to_dict()

    def reject_skill_proposal(
        self, proposal_id: str, *, reason: str = "Rejected from Knowledge"
    ) -> dict[str, Any]:
        path = self.skill_manager._proposal_path(proposal_id)
        proposal = SkillProposal.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if proposal.status == "pending":
            proposal.status = "rejected"
            proposal.metadata = {
                **proposal.metadata,
                "rejection_reason": reason,
                "rejected_at": datetime.now(timezone.utc).isoformat(),
            }
            path.write_text(
                json.dumps(proposal.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            self.skill_manager._record_proposal_index(proposal)
        return proposal.to_dict()

    def _playbook_item(
        self, category: str, entry: Any, index: int | str
    ) -> dict[str, Any]:
        if isinstance(entry, dict):
            content = (
                entry.get("content")
                or entry.get("summary")
                or entry.get("text")
                or json.dumps(entry, ensure_ascii=False)
            )
            timestamp = (
                entry.get("created_at")
                or entry.get("timestamp")
                or entry.get("updated_at")
            )
            metadata = {
                k: v
                for k, v in entry.items()
                if k not in {"content", "summary", "text"}
            }
        else:
            content = str(entry)
            timestamp = None
            metadata = {}
        return {
            "type": "playbook",
            "id": f"playbook:{category}:{index}",
            "category": category,
            "title": str(category).replace("_", " ").title(),
            "content": content,
            "summary": str(content)[:300],
            "created_at": timestamp,
            "metadata": metadata,
        }

    @staticmethod
    def _item_label(item: Any) -> str:
        if isinstance(item, dict):
            return str(
                item.get("title")
                or item.get("name")
                or item.get("proposal_id")
                or item.get("id")
                or item.get("explanation")
                or "knowledge item"
            )
        return str(item)[:80]

    @staticmethod
    def _skill_to_dict(skill: Any) -> dict[str, Any]:
        data = asdict(skill) if hasattr(skill, "__dataclass_fields__") else dict(skill)
        data.setdefault("type", "skill")
        data.setdefault("id", f"skill:{data.get('scope')}:{data.get('name')}")
        data.setdefault("summary", data.get("description") or data.get("title") or "")
        return data

    def _coo_dir(self) -> Path:
        return self.repo_path / ".aider" / "coo"

    def _read_coo_profile(self) -> dict[str, Any]:
        path = self._coo_dir() / "profile.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _read_coo_memory(self, *, limit: int) -> list[dict[str, Any]]:
        path = self._coo_dir() / "memory.jsonl"
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        entry = dict(payload)
                        if "type" in entry:
                            entry["memory_type"] = entry["type"]
                        entry["type"] = "coo_memory"
                        entry["id"] = f"coo-memory:{len(entries)}"
                        entries.append(entry)
        except OSError:
            return []
        return entries[-max(1, limit) :]
