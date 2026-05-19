from __future__ import annotations

from typing import Any

from .store import MemoryStore


def record_outcome(
    store: MemoryStore, record_id: str, outcome: str, context: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    return store.record_outcome(record_id=record_id, outcome=outcome, context=context)

