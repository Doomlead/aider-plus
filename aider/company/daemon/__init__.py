"""Symphony-inspired daemon for tracker-driven Aider Plus Company runs."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from aider.company.schemas import CompanyTask, ProofOfWork
from aider.company.templates import DEFAULT_TEMPLATE_KEY, render_zero_to_mvp_prompt
from aider.company.tracker import (
    TrackerAdapter,
    TrackerError,
    TrackerIssue,
    create_tracker_adapter,
    format_proof_summary,
)
from aider.company.warehouse import default_warehouse_path
from aider.company.workflow import CompanyWorkflow, WorkflowError
from aider.memory.store import MemoryStore


@dataclass(frozen=True)
class SecurityCheckTask:
    """Idle-time daemon task that asks Security to scan the product and platform."""

    task_id: str = field(
        default_factory=lambda: f"security-check-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )
    app_scan_type: str = "vuln"
    platform_scan_type: str = "platform_audit"


class CompanyDaemonError(ValueError):
    """Raised when a Company daemon run cannot proceed."""


@dataclass(frozen=True)
class RunWorkspace:
    key: str
    path: Path
    state_path: Path
    proof_path: Path
    markdown_path: Path


@dataclass
class RunState:
    issue_id: str
    status: str
    workspace: str
    attempts: int = 0
    last_error: str | None = None
    created_at: str = field(default_factory=lambda: _utc_now())
    updated_at: str = field(default_factory=lambda: _utc_now())
    proof_path: str | None = None
    pr_url: str | None = None
    last_proof_link: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunState":
        return cls(
            issue_id=str(data.get("issue_id", "")),
            status=str(data.get("status", "queued")),
            workspace=str(data.get("workspace", "")),
            attempts=int(data.get("attempts", 0) or 0),
            last_error=(
                str(data.get("last_error"))
                if data.get("last_error") is not None
                else None
            ),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
            proof_path=(
                str(data.get("proof_path")) if data.get("proof_path") else None
            ),
            pr_url=(str(data.get("pr_url")) if data.get("pr_url") else None),
            last_proof_link=(
                str(data.get("last_proof_link"))
                if data.get("last_proof_link")
                else None
            ),
        )


class RunWorkspaceManager:
    """Create one isolated, sanitized workspace for each tracker issue."""

    def __init__(self, root: str | Path | None = None):
        if root is None:
            root = default_warehouse_path() / "runs"
        self.root = Path(root).expanduser().resolve()

    def prepare(self, issue: TrackerIssue, *, clean: bool = False) -> RunWorkspace:
        key = sanitize_workspace_key(issue.identifier)
        path = self.root / key
        if clean and path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        self._ensure_git_repo(path)
        state_dir = path / ".aider" / "company"
        state_dir.mkdir(parents=True, exist_ok=True)
        return RunWorkspace(
            key=key,
            path=path,
            state_path=state_dir / "run-state.json",
            proof_path=state_dir / "proof-of-work.json",
            markdown_path=state_dir / "proof-of-work.md",
        )

    @staticmethod
    def _ensure_git_repo(path: Path) -> None:
        if path.joinpath(".git").exists():
            return
        subprocess.run(
            ["git", "init"], cwd=str(path), check=False, capture_output=True, text=True
        )


class CompanyDaemon:
    """Operate issue-backed Company Mode runs with workspaces and proof artifacts."""

    def __init__(
        self,
        *,
        workflow: CompanyWorkflow,
        tracker: TrackerAdapter | None = None,
        workspace_manager: RunWorkspaceManager | None = None,
        runner: Callable[[str, Path, TrackerIssue], dict[str, Any]] | None = None,
        runner_options: Any | None = None,
        orchestrator: Any | None = None,
        coo: Any | None = None,
    ):
        self.workflow = workflow
        self.tracker = tracker or build_tracker(workflow)
        root = workflow.workspace.root
        self.workspace_manager = workspace_manager or RunWorkspaceManager(root)
        self.runner = runner
        self.runner_options = runner_options
        self.orchestrator = orchestrator
        self.coo = coo
        self._default_runner = None

    def configure_runner_options(
        self,
        *,
        departments: tuple[str, ...] = (),
        max_iterations: int | None = None,
        dry_run: bool = False,
    ) -> None:
        from aider.company.daemon.runner import CompanyDaemonRunnerOptions

        self.runner_options = CompanyDaemonRunnerOptions(
            departments=departments,
            max_iterations=max_iterations,
            dry_run=dry_run,
        )
        self._default_runner = None

    def run_once(self, *, dry_run: bool = False) -> list[ProofOfWork]:
        """Process at most max_concurrent_agents currently eligible issues."""

        self.cleanup_workspaces()
        issues = self.tracker.list_candidate_issues(self.workflow.tracker.labels)
        active_workspaces = self._active_workspace_count()
        available_slots = max(
            0, self.workflow.agent.max_concurrent_agents - active_workspaces
        )
        selected = issues[:available_slots]
        proofs: list[ProofOfWork] = []
        for issue in selected:
            proofs.append(self._run_issue_sync(issue, dry_run=dry_run))
        if not issues and not dry_run and active_workspaces == 0:
            self.run_idle_security_check()
        return proofs

    def status(self) -> dict[str, Any]:
        """Return a compact daemon dashboard payload."""

        return self.get_status()

    def get_status(self) -> dict[str, Any]:
        """Return a rich daemon dashboard payload for UIs and COO tools."""

        runs = self._load_run_states()
        active_statuses = {"running", "dry-run"}
        active_runs = [run for run in runs if run.get("status") in active_statuses]
        pending_pow = [
            run
            for run in runs
            if run.get("status") in {"human_review", "failed"}
            or (run.get("status") not in {"done"} and not run.get("proof_path"))
        ]
        last_run = max(
            (str(run.get("updated_at") or run.get("created_at") or "") for run in runs),
            default=None,
        )
        recent_proofs = self._recent_proofs(limit=5)
        configured = self.workflow.path.exists()
        running = bool(active_runs)
        memory_health = (
            MemoryStore(self.orchestrator.state.memory).get_metrics()
            if self.orchestrator is not None
            else {}
        )
        memory_score = float(memory_health.get("memory_health_score", 0.0) or 0.0)
        memory_badge = (
            "🟢" if memory_score >= 80 else ("🟡" if memory_score >= 60 else "🔴")
        )
        return {
            "workflow": str(self.workflow.path),
            "workflow_exists": configured,
            "tracker": self.workflow.tracker.kind,
            "tracker_status": (
                self.tracker.status() if hasattr(self.tracker, "status") else {}
            ),
            "workspace_root": str(self.workspace_manager.root),
            "running": running,
            "status": "running" if running else ("idle" if configured else "missing"),
            "last_run": last_run,
            "active_workflows": len(active_runs),
            "active_runs": active_runs,
            "pending_proof_of_work": len(pending_pow),
            "pending_proof_runs": pending_pow,
            "recent_proof_of_work": recent_proofs,
            "retry_stats": _retry_stats(runs),
            "last_proof_link": _last_proof_link(recent_proofs, runs),
            "max_concurrent_agents": self.workflow.agent.max_concurrent_agents,
            "max_concurrent_workspaces": self.workflow.agent.max_concurrent_agents,
            "available_workspace_slots": max(
                0, self.workflow.agent.max_concurrent_agents - len(active_runs)
            ),
            "hook_timeout_seconds": self.workflow.hooks.timeout_seconds,
            "security_scan_interval_minutes": self.workflow.security.security_scan_interval_minutes,
            "security_scan_backoff_minutes": self.workflow.security.security_scan_backoff_minutes,
            "security_scan_min_frequency_minutes": self.workflow.security.security_scan_min_frequency_minutes,
            "hooks": self.workflow.hooks.names(),
            "safety": {
                "max_concurrent_workspaces": self.workflow.agent.max_concurrent_agents,
                "hook_timeout_seconds": self.workflow.hooks.timeout_seconds,
                "cleanup_completed": self.workflow.workspace.cleanup_completed,
                "max_retained_runs": self.workflow.workspace.max_retained_runs,
                "max_age_days": self.workflow.workspace.max_age_days,
            },
            "runs": runs,
            "security": self._current_security_status(),
            "memory_health": memory_health,
            "memory_health_status": f"Memory Health: {memory_score:.1f}% {memory_badge}",
        }

    def run_idle_security_check(self, *, force: bool = False) -> dict[str, Any]:
        """Run AppSec and PlatformSec checks when idle or manually requested.

        The hard minimum scan frequency is always enforced, even for manual runs.
        """
        task = SecurityCheckTask()
        result = {"task_id": task.task_id, "status": "skipped", "departments": []}
        if self.orchestrator is None or self.coo is None:
            self._get_default_runner()

        current = self._current_security_status()
        current.setdefault(
            "security_scan_interval_minutes",
            self.workflow.security.security_scan_interval_minutes,
        )
        current.setdefault(
            "security_scan_backoff_minutes",
            self.workflow.security.security_scan_backoff_minutes,
        )
        current.setdefault(
            "security_scan_min_frequency_minutes",
            self.workflow.security.security_scan_min_frequency_minutes,
        )
        self._record_security_status(current)
        if current.get("patch_in_progress"):
            result.update(
                {
                    "reason": "security patch already in progress",
                    "last_scan_at": current.get("last_scan_at"),
                    "next_scan_at": current.get("next_scan_at"),
                }
            )
            return result
        if not self._security_scan_fuse_allows(current):
            result.update(
                {
                    "reason": "security scan safety fuse has not elapsed",
                    "last_scan_at": current.get("last_scan_at"),
                    "next_scan_at": current.get("next_scan_at"),
                }
            )
            return result
        if not force and not self._security_scan_due(current):
            result.update(
                {
                    "reason": "security scan interval has not elapsed",
                    "last_scan_at": current.get("last_scan_at"),
                    "next_scan_at": current.get("next_scan_at"),
                }
            )
            return result

        departments = (
            getattr(self.orchestrator, "departments", {}) if self.orchestrator else {}
        )
        if not departments:
            now = _utc_now()
            self._record_security_status(
                {
                    **current,
                    "status": "not_scanned",
                    "severity": "info",
                    "scan_type": "idle_security_check",
                    "finding_count": 0,
                    "last_scan_at": now,
                    "next_scan_at": self._security_next_scan_at(now),
                    "summary": "Idle security check skipped because no Security departments are registered.",
                }
            )
            return result
        try:
            return asyncio.run(self._run_idle_security_check(task))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._run_idle_security_check(task))
            finally:
                loop.close()

    async def _run_idle_security_check(self, task: SecurityCheckTask) -> dict[str, Any]:
        result: dict[str, Any] = {
            "task_id": task.task_id,
            "status": "success",
            "departments": [],
        }
        for department, scan_type in (
            ("security_app", task.app_scan_type),
            ("security_platform", task.platform_scan_type),
        ):
            if department not in getattr(self.orchestrator, "departments", {}):
                continue
            company_task = CompanyTask(
                task_id=f"{task.task_id}:{department}",
                origin="daemon",
                target=department,
                artifact_type="raw_prompt",
                payload={
                    "instruction": "Run an idle-time security scan and report structured findings.",
                    "scan_type": scan_type,
                },
                blocking=False,
                context={"scan_type": scan_type, "security_check_task": task.task_id},
            )
            deliverable = await self.coo.run_department_task(company_task)
            result["departments"].append(
                {
                    "department": department,
                    "status": deliverable.status,
                    "artifact_type": deliverable.artifact_type,
                }
            )
        if not result["departments"]:
            result["status"] = "skipped"
        return result

    def _security_min_frequency_minutes(
        self, current: dict[str, Any] | None = None
    ) -> int:
        current = current or {}
        return int(
            current.get("security_scan_min_frequency_minutes")
            or self.workflow.security.security_scan_min_frequency_minutes
        )

    def _security_scan_interval_minutes(
        self, current: dict[str, Any] | None = None
    ) -> int:
        current = current or {}
        recent_patch = current.get("last_patch_completed_at")
        if (
            recent_patch
            and _minutes_since(recent_patch)
            < self.workflow.security.security_scan_backoff_minutes
        ):
            interval = self.workflow.security.security_scan_backoff_minutes
        else:
            interval = self.workflow.security.security_scan_interval_minutes
        return max(interval, self._security_min_frequency_minutes(current))

    def _security_next_scan_at(self, start_at: str | None = None) -> str:
        base = _parse_utc(start_at) or datetime.now(timezone.utc)
        return (
            base
            + timedelta(
                minutes=self._security_scan_interval_minutes(
                    self._current_security_status()
                )
            )
        ).isoformat()

    def _security_scan_fuse_allows(self, current: dict[str, Any]) -> bool:
        last_scan = current.get("last_scan_at")
        if not last_scan:
            return True
        parsed = _parse_utc(str(last_scan))
        if parsed is None:
            return True
        return datetime.now(timezone.utc) >= parsed + timedelta(
            minutes=self._security_min_frequency_minutes(current)
        )

    def _security_scan_due(self, current: dict[str, Any]) -> bool:
        if not self._security_scan_fuse_allows(current):
            return False
        next_scan = current.get("next_scan_at")
        if not next_scan:
            last_scan = current.get("last_scan_at")
            if not last_scan:
                return True
            parsed = _parse_utc(last_scan)
            if parsed is None:
                return True
            return datetime.now(timezone.utc) >= parsed + timedelta(
                minutes=self._security_scan_interval_minutes(current)
            )
        parsed_next = _parse_utc(str(next_scan))
        return parsed_next is None or datetime.now(timezone.utc) >= parsed_next

    def _record_security_status(self, security: dict[str, Any]) -> None:
        memory = (
            getattr(self.orchestrator, "memory", None) if self.orchestrator else None
        )
        data = getattr(memory, "data", None)
        if isinstance(data, dict):
            data["security"] = security
            persist = getattr(memory, "persist", None)
            if callable(persist):
                try:
                    persist()
                except Exception:
                    pass

    def _current_security_status(self) -> dict[str, Any]:
        memory = (
            getattr(self.orchestrator, "memory", None) if self.orchestrator else None
        )
        data = getattr(memory, "data", None)
        security = data.get("security", {}) if isinstance(data, dict) else {}
        if isinstance(security, dict) and security:
            return dict(security)
        return {"status": "not_scanned", "severity": "info", "finding_count": 0}

    def cleanup_workspaces(self) -> dict[str, Any]:
        """Prune old terminal workspaces when workflow cleanup is enabled."""

        result: dict[str, Any] = {
            "enabled": self.workflow.workspace.cleanup_completed,
            "removed": [],
        }
        if not self.workflow.workspace.cleanup_completed:
            return result
        root = self.workspace_manager.root.resolve()
        if not root.exists():
            return result
        now = datetime.now(timezone.utc)
        terminal_runs = []
        for state_path in root.glob("*/.aider/company/run-state.json"):
            try:
                state = RunState.from_dict(
                    json.loads(state_path.read_text(encoding="utf-8"))
                )
            except Exception:
                continue
            if state.status not in {"done", "failed"}:
                continue
            workspace_path = Path(state.workspace).expanduser().resolve()
            if not _safe_workspace_for_removal(root, workspace_path, state_path):
                continue
            updated_at = _parse_utc(state.updated_at) or now
            terminal_runs.append((updated_at, workspace_path, state))

        max_age = timedelta(days=self.workflow.workspace.max_age_days)
        old_paths = {
            workspace_path
            for updated_at, workspace_path, _state in terminal_runs
            if now - updated_at >= max_age
        }
        terminal_runs.sort(key=lambda item: item[0], reverse=True)
        retained = max(0, self.workflow.workspace.max_retained_runs)
        overflow_paths = {
            workspace_path
            for _updated_at, workspace_path, _state in terminal_runs[retained:]
        }
        for workspace_path in sorted(old_paths | overflow_paths):
            env = dict(os.environ)
            env.update(
                {
                    "AIDER_COMPANY_WORKSPACE": str(workspace_path),
                    "AIDER_COMPANY_WORKFLOW": str(self.workflow.path),
                }
            )
            _check_hook(
                self.workflow.run_hook("before_remove", cwd=workspace_path, env=env),
                "before_remove",
            )
            shutil.rmtree(workspace_path)
            result["removed"].append(str(workspace_path))
        return result

    def _load_run_states(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        if self.workspace_manager.root.exists():
            for state_path in sorted(
                self.workspace_manager.root.glob("*/.aider/company/run-state.json")
            ):
                try:
                    state = RunState.from_dict(
                        json.loads(state_path.read_text(encoding="utf-8"))
                    )
                    runs.append(state.to_dict())
                except Exception as exc:
                    runs.append({"state_path": str(state_path), "error": str(exc)})
        return runs

    def _active_workspace_count(self) -> int:
        return sum(
            1
            for run in self._load_run_states()
            if run.get("status") in {"running", "dry-run"}
        )

    def _recent_proofs(self, *, limit: int = 5) -> list[dict[str, Any]]:
        proofs: list[dict[str, Any]] = []
        if self.workspace_manager.root.exists():
            for proof_path in self.workspace_manager.root.glob(
                "*/.aider/company/proof-of-work.json"
            ):
                try:
                    payload = json.loads(proof_path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        payload["path"] = str(proof_path)
                        proofs.append(payload)
                except Exception as exc:
                    proofs.append({"path": str(proof_path), "error": str(exc)})
        proofs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return proofs[: max(1, limit)]

    async def run_issue(self, issue_id: str, *, dry_run: bool = False) -> ProofOfWork:
        """Claim and run one tracker issue by identifier."""

        issue = self._find_issue(issue_id)
        return await asyncio.to_thread(self._run_issue_sync, issue, dry_run=dry_run)

    def _run_issue_sync(
        self, issue: TrackerIssue, *, dry_run: bool = False
    ) -> ProofOfWork:
        workspace = self.workspace_manager.prepare(
            issue, clean=self.workflow.workspace.clean
        )
        state = self._load_state(workspace, issue)
        if (
            state.attempts >= self.workflow.agent.max_attempts
            and state.status == "failed"
        ):
            return self._write_proof(
                workspace,
                ProofOfWork(
                    issue=issue.identifier,
                    title=issue.title,
                    workspace=str(workspace.path),
                    summary="Skipped because max attempts were already reached.",
                    risk_notes=(state.last_error or "max attempts reached",),
                    retry_count=max(0, state.attempts - 1),
                    last_error=state.last_error,
                ),
            )

        state.status = "dry-run" if dry_run else "running"
        state.attempts += 1
        state.updated_at = _utc_now()
        self._write_state(workspace, state)

        env = self._hook_env(issue, workspace)
        try:
            if not dry_run:
                self.tracker.claim_issue(issue)
                _check_hook(
                    self.workflow.run_hook("after_create", cwd=workspace.path, env=env),
                    "after_create",
                )
                _check_hook(
                    self.workflow.run_hook("before_run", cwd=workspace.path, env=env),
                    "before_run",
                )

            prompt = self._build_company_prompt(issue)
            result = self._run_company_prompt(
                prompt,
                workspace.path,
                issue,
                dry_run=dry_run,
                retry_count=max(0, state.attempts - 1),
                last_error=state.last_error,
            )

            if not dry_run:
                _check_hook(
                    self.workflow.run_hook("after_run", cwd=workspace.path, env=env),
                    "after_run",
                )

            proof = self._proof_from_result(issue, workspace, result, dry_run=dry_run)
            proof = ProofOfWork.from_dict(
                {
                    **proof.to_dict(),
                    "retry_count": max(0, state.attempts - 1),
                    "last_error": None,
                }
            )
            state.status = (
                "human_review"
                if proof.human_review_required or proof.partial_success
                else "done"
            )
            state.proof_path = str(workspace.proof_path)
            state.pr_url = proof.pr_url
            state.last_proof_link = proof.markdown_path or str(workspace.markdown_path)
            state.last_error = None
            state.updated_at = _utc_now()
            self._write_state(workspace, state)
            self._write_proof(workspace, proof)

            if not dry_run:
                if proof.pr_url:
                    self.tracker.attach_pr(issue, proof.pr_url, proof=proof)
                self.tracker.comment(issue, _format_tracker_comment(proof))
                self.tracker.transition(issue, state.status)
            return proof
        except Exception as exc:
            state.status = "failed"
            state.last_error = str(exc)
            state.updated_at = _utc_now()
            self._write_state(workspace, state)
            if not dry_run:
                self.tracker.comment(issue, f"Aider Plus daemon run failed: {exc}")
                self.tracker.transition(
                    issue,
                    (
                        "retry"
                        if state.attempts < self.workflow.agent.max_attempts
                        else "failed"
                    ),
                )
            proof = ProofOfWork(
                issue=issue.identifier,
                title=issue.title,
                workspace=str(workspace.path),
                summary="Run failed before producing a complete Company deliverable.",
                risk_notes=(str(exc),),
                retry_count=max(0, state.attempts - 1),
                last_error=str(exc),
            )
            self._write_proof(workspace, proof)
            return proof

    def _find_issue(self, issue_id: str) -> TrackerIssue:
        for issue in self.tracker.list_candidate_issues(
            ()
        ) + self.tracker.list_candidate_issues(self.workflow.tracker.labels):
            if issue.identifier == issue_id:
                return issue
        raise CompanyDaemonError(f"Issue not found or not eligible: {issue_id}")

    def _get_default_runner(self):
        if self._default_runner is None:
            if self.orchestrator is None:
                from aider.company.orchestrator import CompanyOrchestrator
                from aider.memory import ProjectMemory

                self.orchestrator = CompanyOrchestrator(
                    ProjectMemory(str(self.workspace_manager.root))
                )
            if self.coo is None:
                from aider.company.coo import NanobotCOO

                self.coo = NanobotCOO(orchestrator=self.orchestrator)
            from aider.company.daemon.runner import (
                CompanyDaemonRunner,
                CompanyDaemonRunnerOptions,
            )

            options = self.runner_options or CompanyDaemonRunnerOptions()
            self._default_runner = CompanyDaemonRunner(
                self.orchestrator,
                self.coo,
                timeout_seconds=max(self.workflow.hooks.timeout_seconds, 1),
                options=options,
            )
        return self._default_runner

    def _build_company_prompt(self, issue: TrackerIssue) -> str:
        workflow_prompt = self.workflow.render_prompt(issue)
        template = self.workflow.company.template or DEFAULT_TEMPLATE_KEY
        idea = f"{issue.title}\n\n{issue.description}".strip()
        company_prompt = render_zero_to_mvp_prompt(
            idea=idea,
            template_key=template,
            project_name=issue.identifier,
        )
        return f"{workflow_prompt}\n\n---\n\n{company_prompt}".strip()

    def _run_company_prompt(
        self,
        prompt: str,
        workspace: Path,
        issue: TrackerIssue,
        *,
        dry_run: bool,
        retry_count: int = 0,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        if not dry_run:
            runner = self.runner or self._get_default_runner()
            options = getattr(runner, "options", None)
            if options is not None:
                from dataclasses import replace

                try:
                    runner.options = replace(
                        options, retry_count=retry_count, last_error=last_error
                    )
                except TypeError:
                    pass
            result = runner(prompt, workspace, issue)
            result.setdefault("retry_count", retry_count)
            if last_error is not None:
                result.setdefault("last_error", last_error)
            return result
        prompt_path = workspace / ".aider" / "company" / "daemon-prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        return {
            "summary": (
                "Dry run prepared the workspace and rendered the Company Mode prompt."
                if dry_run
                else "Company prompt rendered by the built-in runner."
            ),
            "changed_files": _git_changed_files(workspace),
            "checks": [],
            "qa_result": "not-run" if dry_run else "pending-runner",
            "review_result": "not-run" if dry_run else "pending-runner",
            "human_review_required": True,
            "retry_count": retry_count,
            "last_error": last_error,
        }

    def _proof_from_result(
        self,
        issue: TrackerIssue,
        workspace: RunWorkspace,
        result: dict[str, Any],
        *,
        dry_run: bool,
    ) -> ProofOfWork:
        return ProofOfWork(
            issue=issue.identifier,
            title=issue.title,
            workspace=str(workspace.path),
            branch=_git_branch(workspace.path),
            pr_url=result.get("pr_url"),
            summary=str(result.get("summary") or "Company daemon run completed."),
            changed_files=tuple(str(item) for item in result.get("changed_files", ())),
            diff_summary=tuple(str(item) for item in result.get("diff_summary", ())),
            commit_messages=tuple(
                str(item) for item in result.get("commit_messages", ())
            ),
            checks=tuple(dict(item) for item in result.get("checks", ())),
            qa_result=str(
                result.get("qa_result") or ("not-run" if dry_run else "unknown")
            ),
            review_result=str(
                result.get("review_result") or ("not-run" if dry_run else "unknown")
            ),
            review_feedback=tuple(
                str(item) for item in result.get("review_feedback", ())
            ),
            delivery_handover=dict(result.get("delivery_handover") or {}),
            devops_status=dict(result.get("devops_status") or {}),
            diffs=tuple(dict(item) for item in result.get("diffs", ())),
            links=tuple(str(item) for item in result.get("links", ())),
            risk_notes=tuple(str(item) for item in result.get("risk_notes", ())),
            completed_stages=tuple(
                str(item) for item in result.get("completed_stages", ())
            ),
            failed_stages=tuple(str(item) for item in result.get("failed_stages", ())),
            partial_stages=tuple(
                str(item) for item in result.get("partial_stages", ())
            ),
            retry_count=int(result.get("retry_count", 0) or 0),
            last_error=(
                str(result.get("last_error"))
                if result.get("last_error") is not None
                else None
            ),
            partial_success=bool(result.get("partial_success", False)),
            human_review_required=bool(result.get("human_review_required", True)),
        )

    def _load_state(self, workspace: RunWorkspace, issue: TrackerIssue) -> RunState:
        if workspace.state_path.exists():
            return RunState.from_dict(
                json.loads(workspace.state_path.read_text(encoding="utf-8"))
            )
        return RunState(
            issue_id=issue.identifier,
            status="queued",
            workspace=str(workspace.path),
        )

    def _write_state(self, workspace: RunWorkspace, state: RunState) -> None:
        workspace.state_path.write_text(
            json.dumps(state.to_dict(), indent=2), encoding="utf-8"
        )

    def _write_proof(self, workspace: RunWorkspace, proof: ProofOfWork) -> ProofOfWork:
        proof = ProofOfWork.from_dict(
            {**proof.to_dict(), "markdown_path": str(workspace.markdown_path)}
        )
        workspace.proof_path.write_text(
            json.dumps(proof.to_dict(), indent=2), encoding="utf-8"
        )
        workspace.markdown_path.write_text(proof.to_markdown(), encoding="utf-8")
        return proof

    def _hook_env(self, issue: TrackerIssue, workspace: RunWorkspace) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "AIDER_COMPANY_ISSUE_ID": issue.identifier,
                "AIDER_COMPANY_ISSUE_TITLE": issue.title,
                "AIDER_COMPANY_WORKSPACE": str(workspace.path),
                "AIDER_COMPANY_WORKFLOW": str(self.workflow.path),
            }
        )
        return env


def _safe_workspace_for_removal(
    root: Path, workspace_path: Path, state_path: Path
) -> bool:
    try:
        workspace_path.relative_to(root)
    except ValueError:
        return False
    expected_state = workspace_path / ".aider" / "company" / "run-state.json"
    return expected_state.resolve() == state_path.resolve() and expected_state.exists()


def build_tracker(workflow: CompanyWorkflow) -> TrackerAdapter:
    try:
        return create_tracker_adapter(workflow.tracker)
    except TrackerError as exc:
        raise CompanyDaemonError(str(exc)) from exc


def sanitize_workspace_key(value: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-_")
    key = re.sub(r"-+", "-", key)
    if not key:
        raise CompanyDaemonError(
            "Issue identifier must contain a workspace-safe character."
        )
    return key[:96]


def load_daemon(workflow_path: str | Path) -> CompanyDaemon:
    workflow = CompanyWorkflow.load(workflow_path)
    return CompanyDaemon(workflow=workflow)


def _check_hook(result: subprocess.CompletedProcess[str] | None, name: str) -> None:
    if result is None:
        return
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()[:1000]
        raise WorkflowError(
            f"Workflow hook {name} failed with exit {result.returncode}: {output}"
        )


def _git_changed_files(path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=str(path),
        text=True,
        capture_output=True,
        check=False,
    )
    files: list[str] = []
    for line in result.stdout.splitlines():
        if line.strip():
            files.append(line[3:].strip() if len(line) > 3 else line.strip())
    return files


def _git_branch(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=str(path),
        text=True,
        capture_output=True,
        check=False,
    )
    branch = result.stdout.strip()
    return branch or None


def _retry_stats(runs: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [int(run.get("attempts", 0) or 0) for run in runs]
    retrying = [
        run for run in runs if run.get("status") in {"failed", "retry", "human_review"}
    ]
    return {
        "total_attempts": sum(attempts),
        "total_retries": sum(max(0, attempts_count - 1) for attempts_count in attempts),
        "retrying_runs": len(retrying),
        "last_error": next(
            (
                run.get("last_error")
                for run in sorted(
                    runs,
                    key=lambda item: str(item.get("updated_at") or ""),
                    reverse=True,
                )
                if run.get("last_error")
            ),
            None,
        ),
    }


def _last_proof_link(
    recent_proofs: list[dict[str, Any]], runs: list[dict[str, Any]]
) -> str | None:
    for proof in recent_proofs:
        link = proof.get("markdown_path") or proof.get("path")
        if link:
            return str(link)
    for run in sorted(
        runs, key=lambda item: str(item.get("updated_at") or ""), reverse=True
    ):
        link = run.get("last_proof_link") or run.get("proof_path")
        if link:
            return str(link)
    return None


def _format_tracker_comment(proof: ProofOfWork) -> str:
    return (
        "Aider Plus Company daemon completed a run.\n\n"
        f"Workspace: {proof.workspace}\n"
        f"Proof of work: {Path(proof.workspace) / '.aider' / 'company' / 'proof-of-work.json'}\n"
        + format_proof_summary(proof, proof.pr_url)
    )


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _minutes_since(value: str | None) -> float:
    parsed = _parse_utc(value)
    if parsed is None:
        return float("inf")
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 60.0)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
