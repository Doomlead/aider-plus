from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    "private_key",
    "client_secret",
}

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.I)),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (
        "phone",
        re.compile(
            r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"
        ),
    ),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
)


@dataclass(frozen=True)
class TenantMemoryPolicy:
    """Tenant-level guardrails enforced before memory records are persisted."""

    tenant_id: str | None = None
    allow_sensitive_data: bool = False
    allow_cross_tenant_recall: bool = False
    blocked_classifications: tuple[str, ...] = ("secret",)
    max_allowed_pii: int = 999


@dataclass(frozen=True)
class ClassificationResult:
    contains_pii: bool = False
    contains_secret: bool = False
    pii_types: tuple[str, ...] = ()
    secret_types: tuple[str, ...] = ()
    classification: str = "internal"
    redacted_text: str | None = None
    policy_violations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def contains_sensitive_data(self) -> bool:
        return self.contains_pii or self.contains_secret

    def to_metadata(self) -> dict[str, Any]:
        return {
            "contains_pii": self.contains_pii,
            "contains_secret": self.contains_secret,
            "contains_sensitive_data": self.contains_sensitive_data,
            "pii_types": list(self.pii_types),
            "secret_types": list(self.secret_types),
            "classification": self.classification,
            "policy_violations": list(self.policy_violations),
        }


def classify_text(
    text: Any, policy: TenantMemoryPolicy | None = None
) -> ClassificationResult:
    raw = str(text or "")
    pii_types = _matches(raw, _PII_PATTERNS)
    secret_types = _matches(raw, _SECRET_PATTERNS)
    classification = "secret" if secret_types else "pii" if pii_types else "internal"
    violations: list[str] = []
    active_policy = policy or TenantMemoryPolicy()
    if secret_types and "secret" in active_policy.blocked_classifications:
        violations.append("secret_blocked")
    if (
        len(pii_types) > active_policy.max_allowed_pii
        and not active_policy.allow_sensitive_data
    ):
        violations.append("pii_blocked")
    redacted = raw
    for label, pattern in (*_SECRET_PATTERNS, *_PII_PATTERNS):
        redacted = pattern.sub(f"[{label.upper()}_REDACTED]", redacted)
    if redacted == raw:
        redacted = None
    return ClassificationResult(
        contains_pii=bool(pii_types),
        contains_secret=bool(secret_types),
        pii_types=tuple(pii_types),
        secret_types=tuple(secret_types),
        classification=classification,
        redacted_text=redacted,
        policy_violations=tuple(violations),
    )


def enforce_tenant_policy(
    *, metadata: dict[str, Any], policy: TenantMemoryPolicy | None = None
) -> None:
    active_policy = policy or TenantMemoryPolicy()
    tenant_id = metadata.get("tenant_id") or metadata.get("tenant")
    if (
        active_policy.tenant_id
        and tenant_id
        and str(tenant_id) != active_policy.tenant_id
    ):
        raise ValueError("memory record tenant_id does not match active tenant policy")
    classification = str(metadata.get("classification") or "internal")
    if (
        classification in active_policy.blocked_classifications
        and not active_policy.allow_sensitive_data
    ):
        raise ValueError(f"memory policy blocks {classification} records")
    violations = metadata.get("policy_violations") or []
    if violations and not active_policy.allow_sensitive_data:
        raise ValueError(f"memory policy violations: {', '.join(map(str, violations))}")


def has_sensitive_signals(metadata: dict[str, Any]) -> bool:
    for key, value in (metadata or {}).items():
        if str(key).lower() in SENSITIVE_KEYS and value:
            return True
    return bool(
        metadata.get("contains_sensitive_data")
        or metadata.get("contains_pii")
        or metadata.get("contains_secret")
    )


def merge_redaction_metadata(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "redacted": False,
        "contains_sensitive_data": False,
        "contains_pii": False,
        "contains_secret": False,
        "redaction_sources": [],
        "pii_types": [],
        "secret_types": [],
    }
    sources: set[str] = set()
    pii: set[str] = set()
    secrets: set[str] = set()
    for record in records:
        metadata = (
            record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        )
        if has_sensitive_signals(metadata):
            merged["contains_sensitive_data"] = True
        if metadata.get("contains_pii"):
            merged["contains_pii"] = True
            pii.update(str(item) for item in metadata.get("pii_types") or [])
        if metadata.get("contains_secret"):
            merged["contains_secret"] = True
            secrets.update(str(item) for item in metadata.get("secret_types") or [])
        if metadata.get("redacted"):
            merged["redacted"] = True
        source = metadata.get("redaction_source")
        if source:
            sources.add(str(source))
    merged["redaction_sources"] = sorted(sources)
    merged["pii_types"] = sorted(pii)
    merged["secret_types"] = sorted(secrets)
    return merged


def ensure_summary_redaction(
    summary_metadata: dict[str, Any], originals: list[dict[str, Any]]
) -> dict[str, Any]:
    merged = merge_redaction_metadata(originals)
    out = dict(summary_metadata or {})
    out.update(merged)
    if merged["contains_sensitive_data"]:
        out["compaction_visibility_guard"] = "sensitive-summary-limited"
    return out


def _matches(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> list[str]:
    found = {label for label, pattern in patterns if pattern.search(text)}
    return sorted(found)
