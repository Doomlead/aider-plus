from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RankingPolicy:
    reinforcement_weight: float = 0.3
    recency_weight: float = 0.2
    min_usage_for_acceptance: int = 1


@dataclass(frozen=True)
class RetentionPolicy:
    """Decay/retention controls for stale-memory mitigation."""

    decay_half_life_days_by_kind: dict[str, int] = field(default_factory=dict)
    decay_half_life_days_by_scope: dict[str, int] = field(default_factory=dict)
    default_decay_half_life_days: int = 30
    min_confidence_for_retention: float = 0.35
    max_cluster_size_before_compaction: int = 12

    def decay_half_life_days(self, *, kind: str | None = None, scope: str | None = None) -> int:
        if kind and kind in self.decay_half_life_days_by_kind:
            return max(1, int(self.decay_half_life_days_by_kind[kind]))
        if scope and scope in self.decay_half_life_days_by_scope:
            return max(1, int(self.decay_half_life_days_by_scope[scope]))
        return max(1, int(self.default_decay_half_life_days))
