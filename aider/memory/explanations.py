from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _freshness_bucket(updated_at: str | None) -> str:
    if not updated_at:
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    if age_days <= 3:
        return "fresh"
    if age_days <= 30:
        return "recent"
    return "stale"


def format_recall_explanation(
    *,
    label: str,
    matching_terms: list[str],
    scope_reason: str,
    confidence: float,
    updated_at: str | None,
    evidence_count: int,
) -> str:
    terms = ", ".join(matching_terms) if matching_terms else "semantic similarity"
    freshness = _freshness_bucket(updated_at)
    return (
        f"{label}: match_terms={terms}; scope_reason={scope_reason}; "
        f"confidence={confidence:.2f}; freshness={freshness}; evidence_count={max(0, int(evidence_count))}."
    )


def explanation_telemetry(
    *,
    label: str,
    matching_terms: list[str],
    scope_reason: str,
    confidence: float,
    updated_at: str | None,
    evidence_count: int,
) -> dict[str, Any]:
    return {
        "label": label,
        "matching_terms": list(matching_terms),
        "scope_reason": scope_reason,
        "confidence": round(float(confidence), 4),
        "freshness": _freshness_bucket(updated_at),
        "updated_at": updated_at or "",
        "evidence_count": max(0, int(evidence_count)),
    }
