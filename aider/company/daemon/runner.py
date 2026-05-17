"""Built-in production runner for Company daemon issue cycles."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aider.company.coo import NanobotCOO
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyEvent, CompanyTask, EventMessage
from aider.company.tracker import TrackerIssue

DEFAULT_DEPARTMENT_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("product", "raw_prompt"),
    ("ux", "prd"),
    ("engineering", "prd"),
    ("qa", "code"),
    ("delivery", "test_report"),
    ("devops", "deploy_request"),
)


@dataclass(frozen=True)
class CompanyDaemonRunnerOptions:
    """Configurable controls for a built-in daemon runner pass."""

    departments: tuple[str, ...] = ()
    max_iterations: int | None = None
    dry_run: bool = False
    continue_on_error: bool = True
    progress_every: int = 1

    def normalized_departments(self) -> tuple[str, ...]:
        return tuple(
            department.strip().lower()
            for department in self.departments
            if department.strip()
        )


@dataclass
class CompanyDaemonRunner:
    """Execute an end-to-end Company Mode cycle for a tracker issue."""

    orchestrator: CompanyOrchestrator
    coo: NanobotCOO
    timeout_seconds: int = 900
    options: CompanyDaemonRunnerOptions = field(
        default_factory=CompanyDaemonRunnerOptions
    )
    run_log: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self, prompt: str, workspace: Path, issue: TrackerIssue
    ) -> dict[str, Any]:
        return self.execute_sync(prompt, workspace, issue)

    def execute_sync(
        self, prompt: str, workspace: Path, issue: TrackerIssue
    ) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute(prompt, workspace, issue))
        raise RuntimeError(
            "CompanyDaemonRunner.execute_sync cannot run inside an active event loop"
        )

    async def execute(
        self, prompt: str, workspace: Path, issue: TrackerIssue
    ) -> dict[str, Any]:
        return await asyncio.wait_for(
            self._execute(prompt, workspace, issue), timeout=self.timeout_seconds
        )

    async def _execute(
        self, prompt: str, workspace: Path, issue: TrackerIssue
    ) -> dict[str, Any]:
        state_dir = workspace / ".aider" / "company"
        state_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = state_dir / "daemon-prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        deliverables: list[Any] = []
        completed_stages: list[str] = []
        failed_stages: list[str] = []
        review_feedback: list[str] = []
        checks: list[dict[str, Any]] = []
        delivery_handover: dict[str, Any] = {}
        devops_status: dict[str, Any] = {}
        risk_notes: list[str] = []

        sequence = self._selected_sequence()
        await self._emit_progress(
            issue,
            stage="start",
            status="running",
            completed_stages=completed_stages,
            failed_stages=failed_stages,
            total_stages=len(sequence),
        )

        departments = self.orchestrator.departments
        if self.options.dry_run:
            risk_notes.append("Runner dry-run requested; no departments were executed.")
        elif departments and sequence:
            payload: Any = prompt
            context: dict[str, Any] = {
                "issue_id": issue.identifier,
                "issue_title": issue.title,
                "issue_url": issue.url,
                "workspace": str(workspace),
                "daemon_prompt_path": str(prompt_path),
            }
            for step_index, (department, artifact_type) in enumerate(sequence, start=1):
                if department not in departments:
                    risk_notes.append(f"Skipped {department}; department is not registered.")
                    continue
                await self._emit_progress(
                    issue,
                    stage=department,
                    status="running",
                    completed_stages=completed_stages,
                    failed_stages=failed_stages,
                    total_stages=len(sequence),
                )
                task = CompanyTask(
                    task_id=f"{issue.identifier}:{department}",
                    origin="daemon" if not deliverables else deliverables[-1].department,
                    target=department,
                    artifact_type=artifact_type,  # type: ignore[arg-type]
                    payload=payload,
                    blocking=False,
                    context=dict(context),
                )
                try:
                    deliverable = await self.coo.run_department_task(task)
                except Exception as exc:
                    failed_stages.append(department)
                    risk_notes.append(f"{department} failed: {exc}")
                    self._record_progress(issue, department, "failed", error=str(exc))
                    await self._emit_progress(
                        issue,
                        stage=department,
                        status="failed",
                        completed_stages=completed_stages,
                        failed_stages=failed_stages,
                        total_stages=len(sequence),
                        error=str(exc),
                    )
                    if not self.options.continue_on_error:
                        break
                    continue

                deliverables.append(deliverable)
                completed_stages.append(department)
                self._record_progress(
                    issue,
                    department,
                    deliverable.status,
                    artifact_type=deliverable.artifact_type,
                )
                payload = deliverable.payload
                if isinstance(deliverable.metadata, dict):
                    context.update(deliverable.metadata.get("context", {}) or {})
                if deliverable.department == "engineering":
                    feedback = deliverable.review_feedback or deliverable.metadata.get(
                        "review_feedback"
                    )
                    if feedback:
                        review_feedback.append(str(feedback))
                if deliverable.department == "qa":
                    checks.append(
                        {
                            "command": deliverable.metadata.get("command", "qa"),
                            "status": deliverable.status,
                            "summary": str(deliverable.payload)[:500],
                        }
                    )
                if deliverable.department == "delivery":
                    handover = deliverable.metadata.get(
                        "delivery_handover"
                    ) or deliverable.metadata.get("project_plan")
                    if isinstance(handover, dict):
                        delivery_handover = handover
                if deliverable.department == "devops":
                    devops_status = {
                        "status": deliverable.status,
                        "artifact_type": deliverable.artifact_type,
                        "metadata": dict(deliverable.metadata),
                        "summary": str(deliverable.payload)[:1000],
                    }
                if deliverable.status not in {"success", "needs_review"}:
                    failed_stages.append(department)
                    risk_notes.append(f"{department} returned {deliverable.status}")
                if self._should_emit_progress(step_index):
                    await self._emit_progress(
                        issue,
                        stage=department,
                        status=deliverable.status,
                        completed_stages=completed_stages,
                        failed_stages=failed_stages,
                        total_stages=len(sequence),
                    )
        elif not departments:
            risk_notes.append(
                "No Company departments were registered; built-in runner rendered the prompt and captured workspace state."
            )
        else:
            risk_notes.append("No departments selected for this daemon runner pass.")

        changed_files = sorted(set(_git_changed_files(workspace)))
        diff = _git_diff(workspace)
        diff_summary = _git_diff_summary(workspace, changed_files)
        commit_messages = _git_commit_messages(workspace)
        qa_deliverable = _last_for_department(deliverables, "qa")
        engineering = _last_for_department(deliverables, "engineering")
        review_result = (
            "passed"
            if engineering and not review_feedback
            else ("feedback" if review_feedback else "not-run")
        )
        qa_result = qa_deliverable.status if qa_deliverable else "not-run"
        partial_success = bool(completed_stages and failed_stages)
        human_review_required = (
            partial_success
            or bool(failed_stages)
            or any(d.status != "success" for d in deliverables)
            or not bool(deliverables)
        )
        diffs = [{"file": "workspace.diff", "diff": diff}] if diff else []
        links = [issue.url] if issue.url else []
        final_status = "partial_success" if partial_success else ("done" if deliverables else "not-run")
        await self._emit_progress(
            issue,
            stage="complete",
            status=final_status,
            completed_stages=completed_stages,
            failed_stages=failed_stages,
            total_stages=len(sequence),
        )
        return {
            "summary": _summarize(issue, deliverables, changed_files, failed_stages),
            "changed_files": changed_files,
            "commit_messages": commit_messages,
            "checks": checks,
            "qa_result": qa_result,
            "review_result": review_result,
            "review_feedback": review_feedback,
            "delivery_handover": delivery_handover,
            "devops_status": devops_status,
            "diffs": diffs,
            "diff_summary": diff_summary,
            "links": links,
            "risk_notes": risk_notes,
            "completed_stages": completed_stages,
            "failed_stages": failed_stages,
            "partial_success": partial_success,
            "human_review_required": human_review_required,
        }

    def _selected_sequence(self) -> tuple[tuple[str, str], ...]:
        selected = set(self.options.normalized_departments())
        sequence = tuple(
            item
            for item in DEFAULT_DEPARTMENT_SEQUENCE
            if not selected or item[0] in selected
        )
        if self.options.max_iterations is not None:
            sequence = sequence[: max(0, self.options.max_iterations)]
        return sequence

    def _record_progress(
        self,
        issue: TrackerIssue,
        department: str,
        status: str,
        *,
        artifact_type: str | None = None,
        error: str | None = None,
    ) -> None:
        event = {
            "issue": issue.identifier,
            "department": department,
            "status": status,
        }
        if artifact_type:
            event["artifact_type"] = artifact_type
        if error:
            event["error"] = error
        self.run_log.append(event)

    def _should_emit_progress(self, step_index: int) -> bool:
        return step_index % max(1, self.options.progress_every) == 0

    async def _emit_progress(
        self,
        issue: TrackerIssue,
        *,
        stage: str,
        status: str,
        completed_stages: list[str],
        failed_stages: list[str],
        total_stages: int,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "name": "daemon_run_progress",
            "issue_id": issue.identifier,
            "stage": stage,
            "status": status,
            "completed_stages": list(completed_stages),
            "failed_stages": list(failed_stages),
            "total_stages": total_stages,
            "completed_count": len(completed_stages),
        }
        if error:
            payload["error"] = error
        emit = getattr(self.orchestrator, "_emit", None)
        if emit is None:
            return
        await emit(
            EventMessage(
                event=CompanyEvent.LIFECYCLE,
                task_id=issue.identifier,
                payload=payload,
                metadata={"department": "daemon"},
            )
        )


def _last_for_department(deliverables: list[Any], department: str) -> Any | None:
    for deliverable in reversed(deliverables):
        if getattr(deliverable, "department", None) == department:
            return deliverable
    return None


def _summarize(
    issue: TrackerIssue,
    deliverables: list[Any],
    changed_files: list[str],
    failed_stages: list[str],
) -> str:
    if not deliverables:
        return (
            f"Prepared built-in Company daemon run for {issue.identifier}; "
            "no departments completed."
        )
    parts = [f"Executed Company daemon cycle for {issue.identifier}."]
    parts.append(
        "Departments: " + ", ".join(f"{d.department}={d.status}" for d in deliverables)
    )
    if failed_stages:
        parts.append(f"Partial success; failed stages: {', '.join(failed_stages)}.")
    if changed_files:
        parts.append(f"Changed files: {', '.join(changed_files[:8])}.")
    return " ".join(parts)


def _git_changed_files(path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=str(path),
        text=True,
        capture_output=True,
        check=False,
    )
    return [
        line[3:].strip() if len(line) > 3 else line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _git_diff(path: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--"],
        cwd=str(path),
        text=True,
        capture_output=True,
        check=False,
    )
    untracked = []
    for file in _git_changed_files(path):
        status = subprocess.run(
            ["git", "status", "--short", "--", file],
            cwd=str(path),
            text=True,
            capture_output=True,
            check=False,
        ).stdout[:2]
        fp = path / file
        if status == "??" and fp.is_file():
            try:
                body = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            untracked.append(
                f"diff --git a/{file} b/{file}\n--- /dev/null\n+++ b/{file}\n"
                + "\n".join(f"+{line}" for line in body.splitlines())
            )
    return "\n".join(part for part in [result.stdout.strip(), *untracked] if part)


def _git_diff_summary(path: Path, changed_files: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--stat", "--"],
        cwd=str(path),
        text=True,
        capture_output=True,
        check=False,
    )
    summary = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    tracked = set()
    for line in summary:
        if "|" in line:
            tracked.add(line.split("|", 1)[0].strip())
    for file in changed_files:
        if file not in tracked:
            status = subprocess.run(
                ["git", "status", "--short", "--", file],
                cwd=str(path),
                text=True,
                capture_output=True,
                check=False,
            ).stdout[:2]
            summary.append(f"{file} — {'new file' if status == '??' else 'changed'}")
    return summary[:25]


def _git_commit_messages(path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--oneline", "-5"],
        cwd=str(path),
        text=True,
        capture_output=True,
        check=False,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]
