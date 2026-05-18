from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
SCOPE_SHARED = "shared"
SCOPE_THREAD = "thread"
SCOPE_ROLE = "role"
SCOPE_SKILL = "skill"
SCOPE_DEPARTMENT = "department"

VALID_SCOPE_PREFIXES = frozenset(
    {
        SCOPE_GLOBAL,
        SCOPE_PROJECT,
        SCOPE_SHARED,
        SCOPE_THREAD,
        SCOPE_ROLE,
        SCOPE_SKILL,
        SCOPE_DEPARTMENT,
    }
)


@dataclass(frozen=True)
class MemoryScope:
    """Parsed memory scope in ``prefix[:name]`` form."""

    prefix: str
    name: Optional[str] = None

    def __str__(self) -> str:
        if self.name:
            return f"{self.prefix}:{self.name}"
        return self.prefix


def parse_scope(scope: str | MemoryScope) -> MemoryScope:
    """Parse and validate a memory scope string."""

    if isinstance(scope, MemoryScope):
        validate_scope(str(scope))
        return scope
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("memory scope must be a non-empty string")

    raw = scope.strip()
    prefix, sep, name = raw.partition(":")
    if not prefix or prefix not in VALID_SCOPE_PREFIXES:
        raise ValueError(
            f"invalid memory scope prefix {prefix!r}; expected one of "
            f"{sorted(VALID_SCOPE_PREFIXES)}"
        )
    if sep and not name:
        raise ValueError("memory scope qualifier must be non-empty")
    if not sep and prefix in {SCOPE_THREAD, SCOPE_ROLE, SCOPE_SKILL, SCOPE_DEPARTMENT}:
        raise ValueError(f"memory scope {prefix!r} requires a qualifier")
    return MemoryScope(prefix=prefix, name=name or None)


def validate_scope(scope: str | MemoryScope) -> str:
    """Return a normalized scope string or raise ``ValueError``."""

    return str(parse_scope(scope))


def scope_matches(record_scope: str, query_scope: str | None) -> bool:
    """Return whether *record_scope* should be included for *query_scope*.

    Global and shared records are intentionally broad; otherwise a query scope
    matches exact records and descendants under the same ``prefix:name`` root.
    """

    record = validate_scope(record_scope)
    if not query_scope:
        return True
    query = validate_scope(query_scope)
    if record in {SCOPE_GLOBAL, SCOPE_SHARED}:
        return True
    return record == query or record.startswith(f"{query}:")
