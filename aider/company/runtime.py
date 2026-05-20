"""Shared runtime entry point for Company run execution across surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

DEFAULT_COMPANY_DEPARTMENT_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("product", "raw_prompt"),
    ("ux", "prd"),
    ("engineering", "prd"),
    ("qa", "code"),
    ("delivery", "test_report"),
    ("devops", "deploy_request"),
)

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


def select_company_department_sequence(
    *,
    selected_departments: tuple[str, ...] = (),
    max_iterations: int | None = None,
) -> tuple[tuple[str, str], ...]:
    selected = {d.strip().lower() for d in selected_departments if d and d.strip()}
    sequence = tuple(
        item for item in DEFAULT_COMPANY_DEPARTMENT_SEQUENCE if not selected or item[0] in selected
    )
    if max_iterations is not None:
        sequence = sequence[: max(0, int(max_iterations))]
    return sequence
