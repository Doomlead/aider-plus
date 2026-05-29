"""Shared runtime entry point for Company run execution across surfaces."""

from __future__ import annotations

from collections.abc import Awaitable, Collection
from dataclasses import dataclass, field
from typing import Any, Callable

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
DepartmentExecutor = Callable[[CompanyTask, dict[str, Any]], Awaitable[Any]]
StageStartCallback = Callable[[int, str, str, int], Awaitable[None]]
StageSuccessCallback = Callable[[int, str, Any, int], Awaitable[None]]
StageErrorCallback = Callable[[int, str, Exception, int], Awaitable[bool]]


@dataclass(frozen=True)
class CompanyRunRequest:
    """Normalized Company run request used by all surfaces."""

    surface: str
    task: CompanyTask
    session_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompanySequenceResult:
    """Result from the runtime-owned department sequence executor."""

    deliverables: list[Any] = field(default_factory=list)
    skipped_departments: list[str] = field(default_factory=list)
    errors: list[tuple[str, Exception]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    final_payload: Any = None
    total_stages: int = 0


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
        item
        for item in DEFAULT_COMPANY_DEPARTMENT_SEQUENCE
        if not selected or item[0] in selected
    )
    if max_iterations is not None:
        sequence = sequence[: max(0, int(max_iterations))]
    return sequence


async def run_company_department_sequence(
    *,
    surface: str,
    session_id: str,
    task_id_prefix: str,
    initial_origin: str,
    initial_payload: Any,
    context: dict[str, Any] | None,
    execute_department: DepartmentExecutor,
    selected_departments: tuple[str, ...] = (),
    max_iterations: int | None = None,
    registered_departments: Collection[str] | None = None,
    on_stage_start: StageStartCallback | None = None,
    on_stage_success: StageSuccessCallback | None = None,
    on_stage_error: StageErrorCallback | None = None,
) -> CompanySequenceResult:
    """Execute the canonical Company department sequence for any surface.

    Daemon, CLI, GUI, and chat surfaces should delegate sequence selection,
    payload handoff, context carry-forward, and ``CompanyRunRequest`` creation to
    this helper so workflow order remains centralized in the runtime layer.
    """

    sequence = select_company_department_sequence(
        selected_departments=selected_departments,
        max_iterations=max_iterations,
    )
    active_context = dict(context or {})
    result = CompanySequenceResult(
        context=active_context,
        final_payload=initial_payload,
        total_stages=len(sequence),
    )
    origin = initial_origin
    payload = initial_payload
    registered = (
        {d.lower() for d in registered_departments} if registered_departments else None
    )

    for step_index, (department, artifact_type) in enumerate(sequence, start=1):
        if registered is not None and department not in registered:
            result.skipped_departments.append(department)
            continue
        if on_stage_start is not None:
            await on_stage_start(step_index, department, artifact_type, len(sequence))

        task = CompanyTask(
            task_id=f"{task_id_prefix}:{department}",
            origin=origin,
            target=department,
            artifact_type=artifact_type,  # type: ignore[arg-type]
            payload=payload,
            blocking=False,
            context=dict(active_context),
        )

        async def _execute(
            req_task: CompanyTask, metadata: dict[str, Any]
        ) -> dict[str, Any]:
            deliverable = await execute_department(req_task, metadata)
            return {"deliverable": deliverable}

        req = CompanyRunRequest(
            surface=surface,
            session_id=session_id,
            task=task,
        )
        try:
            deliverable = (await run_company_task(req, execute=_execute))["deliverable"]
        except Exception as exc:
            result.errors.append((department, exc))
            should_continue = False
            if on_stage_error is not None:
                should_continue = await on_stage_error(
                    step_index, department, exc, len(sequence)
                )
            if should_continue:
                continue
            break

        result.deliverables.append(deliverable)
        result.final_payload = getattr(deliverable, "payload", payload)
        payload = result.final_payload
        origin = getattr(deliverable, "department", department)
        metadata = getattr(deliverable, "metadata", {})
        if isinstance(metadata, dict):
            active_context.update(metadata.get("context", {}) or {})
        if on_stage_success is not None:
            await on_stage_success(step_index, department, deliverable, len(sequence))

    return result


def select_company_entry_target(*, phase: str | None, has_prd: bool) -> str:
    """Return the runtime-owned initial target department for a user run."""

    if str(phase or "").strip().lower() == "prototyping" and not has_prd:
        return "product"
    return "engineering"
