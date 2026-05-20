"""Shared runtime entry point for Company run execution across surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aider.company.schemas import CompanyTask


RunExecutor = Callable[[CompanyTask, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class CompanyRunRequest:
    """Normalized Company run request used by all surfaces."""

    surface: str
    task: CompanyTask
    session_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


async def run_company_task(
    request: CompanyRunRequest,
    *,
    execute: RunExecutor,
) -> dict[str, Any]:
    """Single supported entry point for starting a Company run.

    Surfaces should normalize inbound user input into ``CompanyRunRequest`` and
    invoke this function instead of routing directly to departments or coder
    loops. The concrete execution strategy is injected via ``execute`` to keep
    transport surfaces thin.
    """

    metadata = dict(request.metadata)
    metadata.setdefault("surface", request.surface)
    metadata.setdefault("session_id", request.session_id)
    return await execute(request.task, metadata)
