from __future__ import annotations

from typing import Any

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

