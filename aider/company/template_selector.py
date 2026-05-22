from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aider.company.templates import CANONICAL_TEMPLATE_KEYS, TEMPLATES, detect_template_from_repo
from aider.memory.records import MemoryQuery
from aider.memory.store import MemoryStore


@dataclass(frozen=True)
class SelectionDecision:
    template_key: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    memory_record_ids: list[str] = field(default_factory=list)


def select_template(
    idea: str,
    project_name: str | None,
    role_context: str | None,
    memory_store: MemoryStore,
) -> SelectionDecision:
    """Choose a template key using memory-assisted evidence.

    The selector prefers explicit memory matches first, then falls back to
    repository-shape detection. When evidence is weak, ``custom`` remains a safe
    landing option.
    """

    query_text = " ".join(
        part.strip() for part in (idea, project_name or "", role_context or "") if part
    ).strip()

    if not query_text:
        fallback = detect_template_from_repo()
        return SelectionDecision(
            template_key=fallback,
            confidence=0.55,
            reasons=["No request text; used repository template detection fallback."],
            memory_record_ids=[],
        )

    memory_records = memory_store.query_records(
        MemoryQuery(scope="project", text=query_text, limit=20)
    )
    evidence_scores: dict[str, float] = {key: 0.0 for key in CANONICAL_TEMPLATE_KEYS}
    memory_ids: list[str] = []

    for record in memory_records[:8]:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        template_key = str(metadata.get("template_key") or metadata.get("template") or "").strip()
        if template_key in evidence_scores:
            usage = max(1, int(record.usage_count))
            success_bias = max(0.0, float(record.acceptance_rate))
            evidence_scores[template_key] += 1.0 + (0.5 * success_bias) + (0.1 * usage)
            memory_ids.append(record.id)

    if any(score > 0 for score in evidence_scores.values()):
        ranked = sorted(evidence_scores.items(), key=lambda item: item[1], reverse=True)
        top_key, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = max(0.0, top_score - second_score)
        confidence = min(0.95, 0.45 + (top_score / (top_score + 2.0)) + (0.1 * min(1.0, margin)))
        reasons = [
            f"Selected {top_key} from {len(memory_ids)} matching memory records.",
            f"Evidence margin over next candidate: {margin:.2f}.",
        ]
        return SelectionDecision(
            template_key=top_key,
            confidence=round(confidence, 3),
            reasons=reasons,
            memory_record_ids=memory_ids,
        )

    fallback = detect_template_from_repo()
    fallback_label = TEMPLATES[fallback].label if fallback in TEMPLATES else fallback
    return SelectionDecision(
        template_key=fallback,
        confidence=0.6 if fallback != "custom" else 0.5,
        reasons=[
            "No template-tagged memory evidence matched this request.",
            f"Used repository detection fallback: {fallback_label} ({fallback}).",
        ],
        memory_record_ids=[],
    )
