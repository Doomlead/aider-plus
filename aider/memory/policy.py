from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RankingPolicy:
    reinforcement_weight: float = 0.3
    recency_weight: float = 0.2
    min_usage_for_acceptance: int = 1

