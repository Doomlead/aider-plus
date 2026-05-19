from __future__ import annotations

from collections import defaultdict
from typing import Any

from .records import MemoryRecord

GRAPH_EDGE_KEYS = (
    "co_occurs_with",
    "handoff_from",
    "handoff_to",
    "derived_from",
)


def graph_neighbors(record: MemoryRecord) -> set[str]:
    """Extract normalized graph neighbors from structured metadata edges."""

    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    neighbors: set[str] = set()
    for key in GRAPH_EDGE_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value:
            neighbors.add(value)
        elif isinstance(value, list):
            neighbors.update(str(item) for item in value if item)
    return neighbors


def compute_graph_boosts(records: list[MemoryRecord]) -> dict[str, float]:
    """Compute lightweight centrality + successful handoff path boosts."""

    if not records:
        return {}

    by_id = {record.id: record for record in records}
    inbound: dict[str, int] = defaultdict(int)
    degree: dict[str, int] = {}
    handoff_edges: dict[tuple[str, str], int] = defaultdict(int)

    for record in records:
        neighbors = graph_neighbors(record)
        degree[record.id] = len(neighbors)
        for neighbor in neighbors:
            if neighbor in by_id:
                inbound[neighbor] += 1

        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        sources = metadata.get("handoff_from")
        targets = metadata.get("handoff_to")
        source_ids = [sources] if isinstance(sources, str) else (sources if isinstance(sources, list) else [])
        target_ids = [targets] if isinstance(targets, str) else (targets if isinstance(targets, list) else [])
        if record.successful_uses <= 0:
            continue
        for source in source_ids:
            for target in target_ids:
                if source and target:
                    handoff_edges[(str(source), str(target))] += 1

    max_degree = max(1, max(degree.values() or [0]))
    max_inbound = max(1, max(inbound.values() or [0]))
    max_handoff = max(1, max(handoff_edges.values() or [0]))

    boosts: dict[str, float] = {}
    for record in records:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        centrality = (
            (degree.get(record.id, 0) / max_degree) * 0.6
            + (inbound.get(record.id, 0) / max_inbound) * 0.4
        )

        path_score = 0.0
        sources = metadata.get("handoff_from")
        targets = metadata.get("handoff_to")
        source_ids = [sources] if isinstance(sources, str) else (sources if isinstance(sources, list) else [])
        target_ids = [targets] if isinstance(targets, str) else (targets if isinstance(targets, list) else [])
        for source in source_ids:
            for target in target_ids:
                count = handoff_edges.get((str(source), str(target)), 0)
                if max_handoff:
                    path_score = max(path_score, count / max_handoff)

        boosts[record.id] = round(0.15 * centrality + 0.2 * path_score, 4)

    return boosts
