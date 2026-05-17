"""Built-in production runner for Company daemon issue cycles."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aider.company.coo import NanobotCOO
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyTask
from aider.company.tracker import TrackerIssue


@dataclass
class CompanyDaemonRunner:
    """Execute an end-to-end Company Mode cycle for a tracker issue."""

    orchestrator: CompanyOrchestrator
    coo: NanobotCOO
    timeout_seconds: int = 900
    run_log: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, prompt: str, workspace: Path, issue: TrackerIssue) -> dict[str, Any]:
        return self.execute_sync(prompt, workspace, issue)

    def execute_sync(self, prompt: str, workspace: Path, issue: TrackerIssue) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute(prompt, workspace, issue))
        raise RuntimeError("CompanyDaemonRunner.execute_sync cannot run inside an active event loop")

    async def execute(self, prompt: str, workspace: Path, issue: TrackerIssue) -> dict[str, Any]:
        return await asyncio.wait_for(
            self._execute(prompt, workspace, issue), timeout=self.timeout_seconds
        )

    async def _execute(self, prompt: str, workspace: Path, issue: TrackerIssue) -> dict[str, Any]:
        state_dir = workspace / ".aider" / "company"
        state_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = state_dir / "daemon-prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        deliverables: list[Any] = []
        review_feedback: list[str] = []
        checks: list[dict[str, Any]] = []
        delivery_handover: dict[str, Any] = {}
        devops_status: dict[str, Any] = {}
        risk_notes: list[str] = []

        departments = self.orchestrator.departments
        if departments:
            payload: Any = prompt
            context: dict[str, Any] = {
                "issue_id": issue.identifier,
                "issue_title": issue.title,
                "issue_url": issue.url,
                "workspace": str(workspace),
                "daemon_prompt_path": str(prompt_path),
            }
            sequence = [
                ("product", "raw_prompt"),
                ("ux", "prd"),
                ("engineering", "prd"),
                ("qa", "code"),
                ("delivery", "test_report"),
                ("devops", "deploy_request"),
            ]
            for department, artifact_type in sequence:
                if department not in departments:
                    continue
                task = CompanyTask(
                    task_id=f"{issue.identifier}:{department}",
                    origin="daemon" if not deliverables else deliverables[-1].department,
                    target=department,
                    artifact_type=artifact_type,  # type: ignore[arg-type]
                    payload=payload,
                    blocking=False,
                    context=dict(context),
                )
                deliverable = await self.coo.run_department_task(task)
                deliverables.append(deliverable)
                self.run_log.append(
                    {
                        "issue": issue.identifier,
                        "department": department,
                        "status": deliverable.status,
                        "artifact_type": deliverable.artifact_type,
                    }
                )
                payload = deliverable.payload
                if isinstance(deliverable.metadata, dict):
                    context.update(deliverable.metadata.get("context", {}) or {})
                if deliverable.department == "engineering":
                    feedback = deliverable.review_feedback or deliverable.metadata.get("review_feedback")
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
                    handover = deliverable.metadata.get("delivery_handover") or deliverable.metadata.get("project_plan")
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
                    risk_notes.append(f"{department} returned {deliverable.status}")
        else:
            risk_notes.append(
                "No Company departments were registered; built-in runner rendered the prompt and captured workspace state."
            )

        changed_files = sorted(set(_git_changed_files(workspace)))
        diff = _git_diff(workspace)
        commit_messages = _git_commit_messages(workspace)
        qa_deliverable = _last_for_department(deliverables, "qa")
        engineering = _last_for_department(deliverables, "engineering")
        review_result = "passed" if engineering and not review_feedback else ("feedback" if review_feedback else "not-run")
        qa_result = qa_deliverable.status if qa_deliverable else ("not-run" if not departments else "missing")
        human_review_required = any(d.status != "success" for d in deliverables) or not bool(deliverables)
        diffs = [{"file": "workspace.diff", "diff": diff}] if diff else []
        links = [issue.url] if issue.url else []
        return {
            "summary": _summarize(issue, deliverables, changed_files),
            "changed_files": changed_files,
            "commit_messages": commit_messages,
            "checks": checks,
            "qa_result": qa_result,
            "review_result": review_result,
            "review_feedback": review_feedback,
            "delivery_handover": delivery_handover,
            "devops_status": devops_status,
            "diffs": diffs,
            "links": links,
            "risk_notes": risk_notes,
            "human_review_required": human_review_required,
        }


def _last_for_department(deliverables: list[Any], department: str) -> Any | None:
    for deliverable in reversed(deliverables):
        if getattr(deliverable, "department", None) == department:
            return deliverable
    return None


def _summarize(issue: TrackerIssue, deliverables: list[Any], changed_files: list[str]) -> str:
    if not deliverables:
        return f"Prepared built-in Company daemon run for {issue.identifier}; no departments were registered."
    parts = [f"Executed Company daemon cycle for {issue.identifier}."]
    parts.append("Departments: " + ", ".join(f"{d.department}={d.status}" for d in deliverables))
    if changed_files:
        parts.append(f"Changed files: {', '.join(changed_files[:8])}.")
    return " ".join(parts)


def _git_changed_files(path: Path) -> list[str]:
    result = subprocess.run(["git", "status", "--short"], cwd=str(path), text=True, capture_output=True, check=False)
    return [line[3:].strip() if len(line) > 3 else line.strip() for line in result.stdout.splitlines() if line.strip()]


def _git_diff(path: Path) -> str:
    result = subprocess.run(["git", "diff", "--no-ext-diff", "--"], cwd=str(path), text=True, capture_output=True, check=False)
    untracked = []
    for file in _git_changed_files(path):
        status = subprocess.run(["git", "status", "--short", "--", file], cwd=str(path), text=True, capture_output=True, check=False).stdout[:2]
        fp = path / file
        if status == "??" and fp.is_file():
            try:
                body = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            untracked.append(f"diff --git a/{file} b/{file}\n--- /dev/null\n+++ b/{file}\n" + "\n".join(f"+{line}" for line in body.splitlines()))
    return "\n".join(part for part in [result.stdout.strip(), *untracked] if part)


def _git_commit_messages(path: Path) -> list[str]:
    result = subprocess.run(["git", "log", "--oneline", "-5"], cwd=str(path), text=True, capture_output=True, check=False)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]
