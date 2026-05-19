from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .records import MemoryQuery

from .promotion import record_outcome as _record_outcome
from .store import MemoryStore


class MemoryFabric:
    def __init__(self, store: MemoryStore):
        self.store = store

    def record_outcome(
        self, record_id: str, outcome: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        return _record_outcome(
            self.store, record_id=record_id, outcome=outcome, context=context
        )



    def proactive_recall_prepass(
        self,
        *,
        query: str,
        thread_id: str | None = None,
        channel_id: str | None = None,
        user_id: str | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Return small, intent-biased candidate summaries for recall warmup."""
        intents = self._infer_intents(query)
        scoped = ["project"]
        if thread_id:
            scoped.append(f"thread:{thread_id}")
        if channel_id:
            scoped.append(f"channel:{channel_id}")
        if user_id:
            scoped.append(f"user:{user_id}")

        candidates = []
        for scope in scoped:
            for record in self.store.query_records(MemoryQuery(scope=scope, limit=50)):
                content = str(record.content or "")
                metadata = record.metadata if isinstance(record.metadata, dict) else {}
                reinforcement = int(metadata.get("reinforcement_count") or 0)
                recency = 0.0
                ts = record.updated_at or record.created_at
                if ts:
                    try:
                        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
                        recency = 1.0 / (1.0 + age_days)
                    except ValueError:
                        recency = 0.0
                proximity = sum(1 for i in intents if i and i in content.lower())
                score = (reinforcement * 0.2) + (proximity * 0.5) + (recency * 0.3)
                if score <= 0:
                    continue
                candidates.append({
                    "id": record.id,
                    "scope": record.scope,
                    "summary": content[:240],
                    "score": round(score, 4),
                    "reinforcement": reinforcement,
                    "proximity": proximity,
                    "recency": round(recency, 4),
                })
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[: max(1, min(3, int(limit)))]

    @staticmethod
    def _infer_intents(query: str) -> list[str]:
        parts = [p.strip().lower() for p in str(query).replace("\n", " ").split(" ")]
        return [p for p in parts if len(p) > 3][:8]
