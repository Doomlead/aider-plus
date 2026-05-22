from __future__ import annotations

from dataclasses import dataclass, field
import re
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
    score_breakdown: dict[str, float] = field(default_factory=dict)


CONFIDENCE_THRESHOLD = 0.65
UNCERTAINTY_MARGIN_THRESHOLD = 0.12
MISMATCH_DEMOTION_THRESHOLD = 2


def _tokenize(text: str) -> set[str]:
    return {tok for tok in re.split(r"[^a-z0-9]+", text.lower()) if tok}


def _semantic_template_score(template_key: str, query_text: str) -> float:
    template = TEMPLATES[template_key]
    template_text = " ".join(
        [
            template.key,
            template.label,
            template.description,
            *template.discovery_focus,
            *template.engineering_defaults,
            *template.qa_focus,
        ]
    )
    query_tokens = _tokenize(query_text)
    template_tokens = _tokenize(template_text)
    if not query_tokens or not template_tokens:
        return 0.0
    overlap = len(query_tokens & template_tokens)
    return min(1.0, overlap / max(4, len(query_tokens)))


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
            score_breakdown={fallback: 0.55},
        )

    memory_records = memory_store.query_records(
        MemoryQuery(scope="project", text=query_text, limit=20)
    )
    evidence_scores: dict[str, float] = {}
    reasons_by_template: dict[str, list[str]] = {}
    for key in CANONICAL_TEMPLATE_KEYS:
        semantic = _semantic_template_score(key, query_text)
        evidence_scores[key] = 0.35 * semantic
        reasons_by_template[key] = [
            f"Semantic match score {semantic:.2f} from template description/focus overlap."
        ]
    memory_ids: list[str] = []
    mismatch_counts: dict[str, int] = {key: 0 for key in CANONICAL_TEMPLATE_KEYS}

    for record in memory_records[:8]:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        template_key = str(metadata.get("template_key") or metadata.get("template") or "").strip()
        if template_key in evidence_scores:
            usage = max(1, int(record.usage_count))
            success_bias = max(0.0, float(record.acceptance_rate or 0.0))
            reinforcement = max(0.0, float(record.reinforcement_score or 0.0))
            correction_penalty = max(
                0.0,
                float(
                    metadata.get("correction_penalty")
                    or metadata.get("rewrite_count")
                    or metadata.get("manual_rewrites")
                    or 0.0
                ),
            )
            template_penalty = 0.25 * min(4.0, correction_penalty)
            # user preference: avoid template unless explicitly requested
            preference_penalty = 0.0
            preferences = metadata.get("template_preferences")
            if isinstance(preferences, dict):
                avoid = {str(k).strip().lower() for k, v in preferences.items() if v == "avoid"}
                prefer = {str(k).strip().lower() for k, v in preferences.items() if v == "prefer"}
                if template_key in avoid:
                    preference_penalty += 0.8
                if template_key in prefer:
                    evidence_scores[template_key] += 0.4
            evidence_scores[template_key] += (
                0.9 + (0.6 * success_bias) + (0.4 * reinforcement) + (0.08 * usage)
            )
            evidence_scores[template_key] -= template_penalty + preference_penalty
            if correction_penalty >= 2:
                mismatch_counts[template_key] += 1
            reasons_by_template[template_key].append(
                "Memory evidence contributed success/reinforcement boosts with correction/preference penalties."
            )
            memory_ids.append(record.id)

    if any(score > 0 for score in evidence_scores.values()):
        ranked = sorted(evidence_scores.items(), key=lambda item: item[1], reverse=True)
        top_key, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        if not memory_ids and top_score < 0.30:
            return SelectionDecision(
                template_key="custom",
                confidence=0.5,
                reasons=[
                    "Semantic signal was weak and no memory evidence existed; defaulted to custom.",
                ],
                memory_record_ids=[],
                score_breakdown=dict(evidence_scores),
            )
        margin = max(0.0, top_score - second_score)
        confidence = min(0.95, 0.45 + (top_score / (top_score + 2.0)) + (0.1 * min(1.0, margin)))
        # Hard-stop confidence gate to avoid mismatched scaffolds.
        if mismatch_counts.get(top_key, 0) >= MISMATCH_DEMOTION_THRESHOLD:
            return SelectionDecision(
                template_key="custom",
                confidence=0.5,
                reasons=[
                    f"Top template {top_key} had repeated mismatch evidence; demoted to custom.",
                    f"Mismatch count: {mismatch_counts.get(top_key, 0)}.",
                ],
                memory_record_ids=memory_ids,
                score_breakdown=dict(evidence_scores),
            )
        if confidence < CONFIDENCE_THRESHOLD:
            return SelectionDecision(
                template_key="custom",
                confidence=round(confidence, 3),
                reasons=[
                    f"Top confidence {confidence:.2f} below threshold {CONFIDENCE_THRESHOLD:.2f}; selected custom.",
                ],
                memory_record_ids=memory_ids,
                score_breakdown=dict(evidence_scores),
            )
        if margin < UNCERTAINTY_MARGIN_THRESHOLD:
            return SelectionDecision(
                template_key="custom",
                confidence=round(confidence, 3),
                reasons=[
                    f"Top margin {margin:.2f} below uncertainty threshold {UNCERTAINTY_MARGIN_THRESHOLD:.2f}; selected custom.",
                ],
                memory_record_ids=memory_ids,
                score_breakdown=dict(evidence_scores),
            )
        reasons = [
            f"Selected {top_key} from semantic+memory scoring across {len(memory_records[:8])} records.",
            f"Evidence margin over next candidate: {margin:.2f}.",
            *reasons_by_template.get(top_key, [])[:3],
        ]
        return SelectionDecision(
            template_key=top_key,
            confidence=round(confidence, 3),
            reasons=reasons,
            memory_record_ids=memory_ids,
            score_breakdown=dict(evidence_scores),
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
        score_breakdown={fallback: 0.6 if fallback != "custom" else 0.5},
    )


def select_file_generation_policy(
    *,
    file_path: str,
    request_text: str,
    memory_store: MemoryStore,
) -> dict[str, Any]:
    lowered = f"{file_path} {request_text}".lower()
    if any(token in lowered for token in ("api", "endpoint", "route", "handler")):
        intent = "api_handler"
    elif any(token in lowered for token in ("etl", "pipeline", "transform", "batch")):
        intent = "etl_job"
    elif any(token in lowered for token in ("component", "jsx", "tsx", "react", "vue", "ui")):
        intent = "ui_component"
    elif any(token in lowered for token in ("cli", "command", "argparse", "click", "typer")):
        intent = "cli_command"
    else:
        intent = "generic_file"
    matches = memory_store.query_records(
        MemoryQuery(scope="project", text=f"{intent} {file_path} {request_text}", limit=5)
    )
    evidence_ids = [record.id for record in matches[:3]]
    if len(matches) < 2:
        return {
            "intent": intent,
            "confidence": 0.45,
            "strategy": "neutral_todo_boundaries",
            "memory_evidence_ids": evidence_ids,
            "guidance": (
                "Use a minimal neutral file shape with clear TODO boundaries, "
                "no framework-specific assumptions."
            ),
        }
    return {
        "intent": intent,
        "confidence": 0.75,
        "strategy": "memory_pattern_informed",
        "memory_evidence_ids": evidence_ids,
        "guidance": (
            "Use successful memory patterns for this file intent; keep boundaries explicit "
            "and avoid unrelated framework conventions."
        ),
    }
