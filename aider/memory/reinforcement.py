from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .store import MemoryStore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_skill_outcome(
    store: MemoryStore,
    skill_name: str,
    scope: str,
    task_id: str,
    outcome: str,
    supporting_memory_ids: list[str] | None = None,
) -> dict[str, Any]:
    data = store.project_memory.data
    skills = data.setdefault("skills", {})
    if not isinstance(skills, dict):
        skills = {}
    index = skills.setdefault("reinforcement", {})
    if not isinstance(index, dict):
        index = {}
    key = f"{scope}:{skill_name}"
    now = _utc_now_iso()
    item = index.get(key, {}) if isinstance(index.get(key), dict) else {}
    history = item.get("outcome_history", [])
    if not isinstance(history, list):
        history = []
    is_success = str(outcome).lower() == "success"
    delta = 1 if is_success else -1
    history.append(
        {
            "task_id": task_id,
            "outcome": str(outcome).lower(),
            "delta": delta,
            "supporting_memory_ids": list(supporting_memory_ids or []),
            "recorded_at": now,
        }
    )
    item.update(
        {
            "scope": scope,
            "name": skill_name,
            "last_outcome": str(outcome).lower(),
            "last_task_id": task_id,
            "last_updated_at": now,
            "reinforcement_count": int(item.get("reinforcement_count") or 0)
            + (1 if is_success else 0),
            "failure_count": int(item.get("failure_count") or 0)
            + (0 if is_success else 1),
            "reinforcement_signal": int(item.get("reinforcement_signal") or 0)
            + delta,
            "outcome_history": history[-50:],
            "review_recommended": (
                int(item.get("failure_count") or 0) + (0 if is_success else 1)
            )
            >= 3
            and (
                int(item.get("reinforcement_count") or 0) + (1 if is_success else 0)
            )
            <= 1,
        }
    )
    index[key] = item
    skills["reinforcement"] = index
    data["skills"] = skills
    store.project_memory.update(data)
    store.project_memory.persist()
    return item


def record_memory_outcome(
    store: MemoryStore,
    record_id: str,
    outcome: str,
    related_skill_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    is_success = str(outcome).lower() == "success"
    delta = 1 if is_success else -1
    record = store.reinforce_record(record_id, delta=delta)
    if record is None:
        return None
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    history = metadata.get("outcome_history", [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "outcome": str(outcome).lower(),
            "related_skill_ids": list(related_skill_ids or []),
            "delta": delta,
            "recorded_at": _utc_now_iso(),
        }
    )
    metadata["outcome_history"] = history[-50:]
    metadata["failure_count"] = int(metadata.get("failure_count") or 0) + (
        0 if is_success else 1
    )
    metadata["success_count"] = int(metadata.get("success_count") or 0) + (
        1 if is_success else 0
    )
    metadata["related_skill_ids"] = list(related_skill_ids or [])
    return store.update_record_metadata(record_id, metadata)
