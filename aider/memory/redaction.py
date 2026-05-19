from __future__ import annotations

from typing import Any


SENSITIVE_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "ssn",
    "email",
    "phone",
}


def has_sensitive_signals(metadata: dict[str, Any]) -> bool:
    for key, value in (metadata or {}).items():
        if str(key).lower() in SENSITIVE_KEYS and value:
            return True
    return bool(metadata.get("contains_sensitive_data"))


def merge_redaction_metadata(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "redacted": False,
        "contains_sensitive_data": False,
        "redaction_sources": [],
    }
    sources: set[str] = set()
    for record in records:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        if has_sensitive_signals(metadata):
            merged["contains_sensitive_data"] = True
        if metadata.get("redacted"):
            merged["redacted"] = True
        source = metadata.get("redaction_source")
        if source:
            sources.add(str(source))
    merged["redaction_sources"] = sorted(sources)
    return merged


def ensure_summary_redaction(summary_metadata: dict[str, Any], originals: list[dict[str, Any]]) -> dict[str, Any]:
    merged = merge_redaction_metadata(originals)
    out = dict(summary_metadata or {})
    out.update(merged)
    if merged["contains_sensitive_data"]:
        out["compaction_visibility_guard"] = "sensitive-summary-limited"
    return out
