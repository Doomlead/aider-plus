from __future__ import annotations

from .records import MemoryQuery, MemoryRecord
from .scopes import SCOPE_GLOBAL, SCOPE_SHARED, parse_scope, validate_scope

VISIBILITY_PUBLIC = "public"
VISIBILITY_TEAM = "team"
VISIBILITY_PRIVATE = "private"
VISIBILITY_SKILL = "skill"

VALID_VISIBILITIES = frozenset(
    {VISIBILITY_PUBLIC, VISIBILITY_TEAM, VISIBILITY_PRIVATE, VISIBILITY_SKILL}
)


def validate_visibility(visibility: str | None) -> str:
    value = visibility or VISIBILITY_TEAM
    if value not in VALID_VISIBILITIES:
        raise ValueError(
            f"invalid memory visibility {value!r}; expected one of "
            f"{sorted(VALID_VISIBILITIES)}"
        )
    return value


def is_visible(record: MemoryRecord, query: MemoryQuery | None = None) -> bool:
    """Apply Phase 1 visibility rules for a record/query pair."""

    visibility = validate_visibility(record.visibility)
    if visibility == VISIBILITY_PUBLIC:
        return True

    query = query or MemoryQuery()
    requester_scope = query.requester_scope or query.scope
    if visibility == VISIBILITY_PRIVATE:
        if query.requester and record.author and query.requester == record.author:
            return True
        return bool(requester_scope and validate_scope(requester_scope) == record.scope)

    if visibility == VISIBILITY_SKILL:
        return _skill_visible(record, requester_scope)

    # Team visibility: shared/global records are broadly visible; qualified role
    # and department scopes require the same qualified scope.
    if record.scope in {SCOPE_GLOBAL, SCOPE_SHARED}:
        return True
    if not requester_scope:
        return True
    requester = parse_scope(requester_scope)
    rec = parse_scope(record.scope)
    if rec.prefix in {SCOPE_GLOBAL, SCOPE_SHARED}:
        return True
    if rec.prefix == "project":
        return requester.prefix in {
            "project",
            "role",
            "department",
            "thread",
            "channel",
            "user",
            "skill",
        }
    return rec == requester


def filter_visible(
    records: list[MemoryRecord], query: MemoryQuery | None = None
) -> list[MemoryRecord]:
    return [record for record in records if is_visible(record, query)]


def _skill_visible(record: MemoryRecord, requester_scope: str | None) -> bool:
    if record.scope.startswith("skill:") or record.scope == SCOPE_SHARED:
        return True
    if not requester_scope:
        return False
    requester = parse_scope(requester_scope)
    rec = parse_scope(record.scope)
    return requester.prefix == "skill" or rec == requester
