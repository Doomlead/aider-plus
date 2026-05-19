from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .records import MemoryQuery, MemoryRecord
from .scopes import SCOPE_GLOBAL, SCOPE_SHARED, parse_scope, validate_scope

VISIBILITY_PRIVATE = "private"
VISIBILITY_CHANNEL = "channel"
VISIBILITY_PROJECT = "project"
VISIBILITY_USER_VISIBLE = "user_visible"
VISIBILITY_SYSTEM = "system"

# Legacy names accepted for backward compatibility with memory written before the
# canonical visibility vocabulary landed.
VISIBILITY_PUBLIC_LEGACY = "public"
VISIBILITY_TEAM_LEGACY = "team"
VISIBILITY_SKILL_LEGACY = "skill"

VALID_VISIBILITIES = frozenset(
    {
        VISIBILITY_PRIVATE,
        VISIBILITY_CHANNEL,
        VISIBILITY_PROJECT,
        VISIBILITY_USER_VISIBLE,
        VISIBILITY_SYSTEM,
    }
)
LEGACY_VISIBILITY_ALIASES = {
    VISIBILITY_PUBLIC_LEGACY: VISIBILITY_PROJECT,
    VISIBILITY_TEAM_LEGACY: VISIBILITY_PROJECT,
    VISIBILITY_SKILL_LEGACY: VISIBILITY_PROJECT,
}
ALL_VISIBILITIES = frozenset((*VALID_VISIBILITIES, *LEGACY_VISIBILITY_ALIASES))


def normalize_visibility(visibility: str | None) -> str:
    """Return the canonical visibility value, accepting legacy persisted aliases."""

    value = visibility or VISIBILITY_PROJECT
    canonical = LEGACY_VISIBILITY_ALIASES.get(value, value)
    if canonical not in VALID_VISIBILITIES:
        raise ValueError(
            f"invalid memory visibility {value!r}; expected one of "
            f"{sorted(VALID_VISIBILITIES)}"
        )
    return canonical


def validate_visibility(visibility: str | None, *, allow_legacy: bool = True) -> str:
    """Return a canonical visibility value or raise for unknown/legacy writes."""

    value = visibility or VISIBILITY_PROJECT
    if not allow_legacy and value in LEGACY_VISIBILITY_ALIASES:
        raise ValueError(
            f"legacy memory visibility {value!r} is not accepted on write; use "
            f"{LEGACY_VISIBILITY_ALIASES[value]!r}"
        )
    return normalize_visibility(value)


def is_visible(record: "MemoryRecord", query: "MemoryQuery" | None = None) -> bool:
    """Apply canonical memory visibility rules for a record/query pair."""

    visibility = normalize_visibility(record.visibility)
    if visibility == VISIBILITY_SYSTEM:
        return True

    if query is None:
        from .records import MemoryQuery

        query = MemoryQuery()
    requester_scope = query.requester_scope or query.scope
    record_scope = validate_scope(record.scope)

    if visibility == VISIBILITY_PRIVATE:
        if query.requester and record.author and query.requester == record.author:
            return True
        return bool(requester_scope and validate_scope(requester_scope) == record_scope)

    if visibility == VISIBILITY_USER_VISIBLE:
        if query.requester and record.author and query.requester == record.author:
            return True
        if not requester_scope:
            return False
        requester = parse_scope(requester_scope)
        rec = parse_scope(record_scope)
        return requester.prefix == "user" and requester == rec

    if visibility == VISIBILITY_CHANNEL:
        if not requester_scope:
            return record_scope in {SCOPE_GLOBAL, SCOPE_SHARED}
        requester = parse_scope(requester_scope)
        rec = parse_scope(record_scope)
        if rec.prefix in {SCOPE_GLOBAL, SCOPE_SHARED}:
            return True
        if rec.prefix == "channel":
            return (requester.prefix == "channel" and requester == rec) or (
                requester.prefix in {"department", "role"}
                and requester.name in str(rec.name or "").split(":")
            )
        if rec.prefix == "channel_pair":
            return requester.prefix in {"department", "role"} and requester.name in str(
                rec.name or ""
            ).split(":")
        return rec == requester

    # Project visibility: broadly visible inside the project, but records scoped
    # to a specific department/role/thread/channel/user/skill are only returned
    # when the requester asks from that exact qualified scope.
    if record_scope in {SCOPE_GLOBAL, SCOPE_SHARED}:
        return True
    if not requester_scope:
        return True
    requester = parse_scope(requester_scope)
    rec = parse_scope(record_scope)
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
    if rec.prefix in {"channel", "channel_pair"}:
        return (requester.prefix == rec.prefix and requester == rec) or (
            requester.prefix in {"department", "role"}
            and requester.name in str(rec.name or "").split(":")
        )
    return rec == requester


def filter_visible(
    records: list["MemoryRecord"], query: "MemoryQuery" | None = None
) -> list["MemoryRecord"]:
    return [record for record in records if is_visible(record, query)]
