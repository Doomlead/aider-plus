from __future__ import annotations

from .store import MemoryStore


def rebuild_after_compaction_batch(store: MemoryStore, *, compacted_count: int) -> int:
    """Rebuild search indexes after a non-empty compaction batch."""

    count = max(0, int(compacted_count))
    if count <= 0:
        return 0
    store.rebuild_index()
    return count
