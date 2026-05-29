import json
from pathlib import Path

from aider.company.cli import handle_company_cli_pre_coder, parse_company_cli
from aider.company.daemon import (
    CompanyDaemon,
    RunWorkspaceManager,
    sanitize_workspace_key,
)
from aider.company.tracker import LocalJsonTrackerAdapter
from aider.company.workflow import CompanyWorkflow


def write_workflow(tmp_path: Path, tracker_path: Path, runs_path: Path) -> Path:
    workflow_path = tmp_path / "AIDER_WORKFLOW.md"
    workflow_path.write_text(
        f"""---
tracker:
  kind: local
  path: {tracker_path}
  labels: [aider-plus]
workspace:
  root: {runs_path}
agent:
  max_concurrent_agents: 2
  max_attempts: 2
company:
  template: python-cli
hooks:
  after_create: |
    echo created > hook.txt
---
Work on {{{{ issue.identifier }}}}: {{{{ issue.title }}}}
""",
        encoding="utf-8",
    )
    return workflow_path


def test_workflow_loads_repo_owned_policy(tmp_path):
    workflow_path = write_workflow(
        tmp_path, tmp_path / "issues.json", tmp_path / "runs"
    )
    workflow = CompanyWorkflow.load(workflow_path)

    assert workflow.tracker.kind == "local"
    assert workflow.tracker.labels == ("aider-plus",)
    assert workflow.agent.max_concurrent_agents == 2
    assert workflow.agent.max_attempts == 2
    assert "issue.identifier" in workflow.prompt


def test_local_tracker_claim_comment_transition_and_pr(tmp_path):
    tracker_path = tmp_path / "issues.json"
    tracker_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "identifier": "AP-1",
                        "title": "Build settings page",
                        "labels": ["aider-plus"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    tracker = LocalJsonTrackerAdapter(tracker_path)
    issue = tracker.list_candidate_issues(("aider-plus",))[0]

    claimed = tracker.claim_issue(issue)
    tracker.comment(claimed, "Working on it")
    tracker.attach_pr(claimed, "https://example.test/pr/1")
    tracker.transition(claimed, "human_review")

    data = json.loads(tracker_path.read_text(encoding="utf-8"))
    raw = data["issues"][0]
    assert raw["status"] == "human_review"
    assert raw["comments"][0]["body"] == "Working on it"
    assert raw["pull_requests"][0]["url"].endswith("/pr/1")


def test_daemon_dry_run_creates_workspace_prompt_state_and_proof(tmp_path):
    tracker_path = tmp_path / "issues.json"
    runs_path = tmp_path / "runs"
    tracker_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "identifier": "AP-123/unsafe chars",
                        "title": "Add billing reports",
                        "description": "Need a dashboard export.",
                        "labels": ["aider-plus"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workflow = CompanyWorkflow.load(write_workflow(tmp_path, tracker_path, runs_path))
    daemon = CompanyDaemon(workflow=workflow)

    proofs = daemon.run_once(dry_run=True)

    assert len(proofs) == 1
    proof = proofs[0]
    workspace = Path(proof.workspace)
    assert workspace.name == "AP-123-unsafe-chars"
    assert workspace.joinpath(".git").exists()
    assert workspace.joinpath(".aider", "company", "daemon-prompt.md").exists()
    assert workspace.joinpath(".aider", "company", "run-state.json").exists()
    assert workspace.joinpath(".aider", "company", "proof-of-work.json").exists()
    assert "Dry run prepared" in proof.summary
    prompt = workspace.joinpath(".aider", "company", "daemon-prompt.md").read_text(
        encoding="utf-8"
    )
    assert "AP-123/unsafe chars" in prompt
    assert "Product template: Python CLI (python-cli)" in prompt


def test_daemon_runner_updates_tracker_and_status(tmp_path):
    tracker_path = tmp_path / "issues.json"
    runs_path = tmp_path / "runs"
    tracker_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "identifier": "AP-2",
                        "title": "Improve onboarding",
                        "labels": ["aider-plus"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workflow = CompanyWorkflow.load(write_workflow(tmp_path, tracker_path, runs_path))

    def runner(prompt, workspace, issue):
        workspace.joinpath("result.txt").write_text(prompt, encoding="utf-8")
        return {
            "summary": f"Implemented {issue.identifier}",
            "changed_files": ["result.txt"],
            "checks": [{"command": "pytest", "status": "passed"}],
            "qa_result": "passed",
            "review_result": "passed",
            "human_review_required": False,
            "pr_url": "https://example.test/pr/2",
        }

    daemon = CompanyDaemon(workflow=workflow, runner=runner)
    proofs = daemon.run_once()

    assert proofs[0].human_review_required is False
    data = json.loads(tracker_path.read_text(encoding="utf-8"))
    raw = data["issues"][0]
    assert raw["status"] == "done"
    assert raw["comments"]
    assert raw["pull_requests"][0]["url"].endswith("/pr/2")
    status = daemon.status()
    assert status["runs"][0]["status"] == "done"


def test_company_daemon_cli_dry_run(tmp_path, capsys):
    tracker_path = tmp_path / "issues.json"
    tracker_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "identifier": "AP-3",
                        "title": "Ship docs",
                        "labels": ["aider-plus"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workflow_path = write_workflow(tmp_path, tracker_path, tmp_path / "runs")
    command, aider_args = parse_company_cli(
        ["company", "daemon", "--workflow", str(workflow_path), "--once", "--dry-run"]
    )

    assert aider_args == []
    assert command.action == "daemon"
    assert command.workflow_path == str(workflow_path)
    assert handle_company_cli_pre_coder(command) == 0
    out = capsys.readouterr().out
    assert "Issue: AP-3" in out
    assert "Proof of work:" in out


def test_sanitize_workspace_key_rejects_empty_identifier():
    try:
        sanitize_workspace_key("///")
    except ValueError as exc:
        assert "workspace-safe" in str(exc)
    else:
        raise AssertionError("Expected invalid workspace key")


def test_builtin_daemon_runner_end_to_end_tracker_and_proof(tmp_path):
    import asyncio

    from aider.company.coo import NanobotCOO
    from aider.company.daemon.runner import CompanyDaemonRunner
    from aider.company.department import Department
    from aider.company.orchestrator import CompanyOrchestrator
    from aider.company.schemas import CompanyTask, Deliverable, ProofOfWork
    from aider.memory import ProjectMemory

    class CycleDepartment(Department):
        def __init__(self, memory, name):
            super().__init__(memory)
            self.name = name

        async def process(self, task: CompanyTask) -> Deliverable:
            workspace = Path(task.context["workspace"])
            metadata = {"context": task.context}
            payload = f"{self.name} completed {task.task_id}"
            if self.name == "engineering":
                workspace.joinpath("feature.txt").write_text(
                    "implemented", encoding="utf-8"
                )
                metadata["review_feedback"] = "Reviewer approved implementation."
            if self.name == "qa":
                metadata["command"] = "pytest tests/company/test_symphony_daemon.py"
            if self.name == "delivery":
                metadata["delivery_handover"] = {
                    "ready_for_devops": True,
                    "release_scope": "daemon test",
                }
            if self.name == "devops":
                metadata["build"] = "passed"
                metadata["deploy"] = "skipped-test"
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type=task.artifact_type,
                payload=payload,
                status="success",
                metadata=metadata,
                review_feedback=(
                    "Reviewer approved implementation."
                    if self.name == "engineering"
                    else None
                ),
            )

    tracker_path = tmp_path / "issues.json"
    runs_path = tmp_path / "runs"
    tracker_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "identifier": "AP-4",
                        "title": "Run full company cycle",
                        "description": "Exercise built-in runner.",
                        "labels": ["aider-plus"],
                        "url": "https://example.test/AP-4",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workflow = CompanyWorkflow.load(write_workflow(tmp_path, tracker_path, runs_path))
    memory = ProjectMemory(str(tmp_path / "memory"))
    orchestrator = CompanyOrchestrator(memory)
    for name in ("product", "engineering", "qa", "delivery", "devops"):
        orchestrator.register(CycleDepartment(memory, name))
    coo = NanobotCOO(orchestrator=orchestrator)
    runner = CompanyDaemonRunner(orchestrator, coo, timeout_seconds=5)
    daemon = CompanyDaemon(
        workflow=workflow, orchestrator=orchestrator, coo=coo, runner=runner
    )

    proof = asyncio.run(daemon.run_issue("AP-4"))

    assert proof.human_review_required is False
    assert "feature.txt" in proof.changed_files
    assert proof.qa_result == "success"
    assert proof.review_result == "feedback"
    assert proof.delivery_handover["ready_for_devops"] is True
    assert proof.devops_status["status"] == "success"
    assert proof.diffs and "feature.txt" in proof.diffs[0]["diff"]
    workspace = Path(proof.workspace)
    markdown = workspace.joinpath(".aider", "company", "proof-of-work.md").read_text(
        encoding="utf-8"
    )
    assert "# Proof of Work: AP-4" in markdown
    assert "## DevOps Build/Deploy" in markdown
    reloaded = ProofOfWork.from_dict(
        json.loads(
            workspace.joinpath(".aider", "company", "proof-of-work.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert reloaded.markdown_path.endswith("proof-of-work.md")
    data = json.loads(tracker_path.read_text(encoding="utf-8"))
    raw = data["issues"][0]
    assert raw["status"] == "done"
    assert "Proof report:" in raw["comments"][0]["body"]


def test_builtin_runner_partial_success_continues_and_emits_progress(tmp_path):
    import asyncio

    from aider.company.coo import NanobotCOO
    from aider.company.daemon.runner import (
        CompanyDaemonRunner,
        CompanyDaemonRunnerOptions,
    )
    from aider.company.department import Department
    from aider.company.orchestrator import CompanyOrchestrator
    from aider.company.schemas import CompanyTask, Deliverable
    from aider.company.tracker import TrackerIssue
    from aider.memory import ProjectMemory

    class PartialDepartment(Department):
        def __init__(self, memory, name):
            super().__init__(memory)
            self.name = name

        async def process(self, task: CompanyTask) -> Deliverable:
            workspace = Path(task.context["workspace"])
            if self.name == "engineering":
                workspace.joinpath("partial.txt").write_text(
                    "partial", encoding="utf-8"
                )
            metadata = {"context": task.context}
            if self.name == "qa":
                metadata["command"] = "pytest failing-test"
                status = "failure"
                payload = "QA found a regression"
            elif self.name == "delivery":
                metadata["delivery_handover"] = {"ready_for_devops": False}
                status = "success"
                payload = "Delivery recorded follow-up blockers"
            else:
                status = "success"
                payload = f"{self.name} complete"
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type=task.artifact_type,
                payload=payload,
                status=status,
                metadata=metadata,
            )

    async def run():
        memory = ProjectMemory(str(tmp_path / "memory"))
        orchestrator = CompanyOrchestrator(memory)
        events = []

        async def capture(message):
            events.append(message)

        orchestrator.on_deliverable(capture)
        for name in ("engineering", "qa", "delivery"):
            orchestrator.register(PartialDepartment(memory, name))
        coo = NanobotCOO(orchestrator=orchestrator)
        runner = CompanyDaemonRunner(
            orchestrator,
            coo,
            timeout_seconds=5,
            options=CompanyDaemonRunnerOptions(
                departments=("engineering", "qa", "delivery"),
                max_iterations=3,
            ),
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        RunWorkspaceManager._ensure_git_repo(workspace)
        result = await runner.execute(
            "Implement a partial run",
            workspace,
            TrackerIssue(identifier="AP-5", title="Partial success"),
        )
        return result, events

    result, events = asyncio.run(run())

    assert result["partial_success"] is True
    assert result["completed_stages"] == ["engineering", "qa", "delivery"]
    assert result["failed_stages"] == ["qa"]
    assert result["qa_result"] == "failure"
    assert "partial.txt" in result["changed_files"]
    assert any("partial.txt" in line for line in result["diff_summary"])
    progress = [
        event
        for event in events
        if isinstance(getattr(event, "payload", None), dict)
        and event.payload.get("name") == "daemon_run_progress"
    ]
    assert progress
    assert progress[-1].payload["status"] == "partial_success"


def test_builtin_runner_delegates_department_sequence_to_orchestrator(tmp_path):
    import asyncio

    from aider.company.coo import NanobotCOO
    from aider.company.daemon.runner import (
        CompanyDaemonRunner,
        CompanyDaemonRunnerOptions,
    )
    from aider.company.department import Department
    from aider.company.orchestrator import CompanyOrchestrator
    from aider.company.schemas import CompanyTask, Deliverable
    from aider.company.tracker import TrackerIssue
    from aider.memory import ProjectMemory

    class EngineeringDepartment(Department):
        name = "engineering"

        async def process(self, task: CompanyTask) -> Deliverable:
            Path(task.context["workspace"]).joinpath("sequenced.txt").write_text(
                "orchestrated", encoding="utf-8"
            )
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type=task.artifact_type,
                payload="done",
                status="success",
                metadata={"context": task.context},
            )

    memory = ProjectMemory(str(tmp_path / "memory"))
    orchestrator = CompanyOrchestrator(memory)
    orchestrator.register(EngineeringDepartment(memory))
    original_count = orchestrator.department_sequence_stage_count
    original_run = orchestrator.run_department_sequence
    calls = []

    def count_wrapper(**kwargs):
        calls.append(("count", kwargs))
        return original_count(**kwargs)

    async def run_wrapper(**kwargs):
        calls.append(("run", kwargs))
        return await original_run(**kwargs)

    orchestrator.department_sequence_stage_count = count_wrapper
    orchestrator.run_department_sequence = run_wrapper
    coo = NanobotCOO(orchestrator=orchestrator)
    runner = CompanyDaemonRunner(
        orchestrator,
        coo,
        timeout_seconds=5,
        options=CompanyDaemonRunnerOptions(departments=("engineering",)),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    RunWorkspaceManager._ensure_git_repo(workspace)

    result = asyncio.run(
        runner.execute(
            "Implement delegated sequencing",
            workspace,
            TrackerIssue(identifier="AP-SEQ", title="Delegate sequencing"),
        )
    )

    assert not hasattr(runner, "_selected_sequence")
    assert [name for name, _kwargs in calls] == ["count", "run"]
    assert calls[0][1]["selected_departments"] == ("engineering",)
    assert calls[1][1]["surface"] == "daemon"
    assert result["completed_stages"] == ["engineering"]
    assert result["human_review_required"] is False
    assert "sequenced.txt" in result["changed_files"]


def test_company_daemon_cli_parses_runner_options(tmp_path):
    workflow_path = write_workflow(
        tmp_path, tmp_path / "issues.json", tmp_path / "runs"
    )
    command, aider_args = parse_company_cli(
        [
            "company",
            "daemon",
            "--workflow",
            str(workflow_path),
            "--run",
            "AP-6",
            "--departments",
            "product,engineering,qa",
            "--max-iterations",
            "2",
        ]
    )

    assert aider_args == []
    assert command.run_issue_id == "AP-6"
    assert command.runner_departments == ("product", "engineering", "qa")
    assert command.runner_max_iterations == 2


def test_daemon_github_adapter_mocked_round_trip():
    import httpx

    from aider.company.tracker.github import GitHubTrackerAdapter

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/issues"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 42,
                        "number": 42,
                        "title": "Ship invite rollout",
                        "body": "Release via Company daemon.",
                        "state": "open",
                        "labels": [{"name": "aider-plus"}],
                        "html_url": "https://github.test/owner/repo/issues/42",
                    }
                ],
            )
        if request.method == "PATCH" and request.url.path.endswith("/issues/42"):
            body = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "number": 42,
                    "title": "Ship invite rollout",
                    "body": "Release via Company daemon.",
                    "state": body.get("state", "open"),
                    "labels": [{"name": label} for label in body.get("labels", [])],
                },
            )
        if request.method == "POST" and request.url.path.endswith(
            "/issues/42/comments"
        ):
            return httpx.Response(201, json={"id": 1001})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.github.test"
    )
    tracker = GitHubTrackerAdapter(
        token="token",
        repo="owner/repo",
        api_url="https://api.github.test",
        client=client,
    )

    issue = tracker.list_candidate_issues(("aider-plus",))[0]
    claimed = tracker.claim_issue(issue)
    tracker.comment(claimed, "Company daemon started.")
    tracker.attach_pr(claimed, "https://github.test/owner/repo/pull/7")
    done = tracker.transition(claimed, "done")

    assert issue.identifier == "42"
    assert claimed.status == "in_progress"
    assert done.status == "done"
    request_kinds = [(request.method, request.url.path) for request in requests]
    assert ("GET", "/repos/owner/repo/issues") in request_kinds
    assert sum(1 for method, _path in request_kinds if method == "POST") == 2
    assert any(
        request.method == "PATCH"
        and json.loads(request.content.decode()).get("state") == "closed"
        for request in requests
    )


def test_security_scan_safety_fuse_blocks_too_frequent_manual_runs(tmp_path):
    from datetime import datetime, timezone

    from aider.company.coo import NanobotCOO
    from aider.company.department import Department
    from aider.company.orchestrator import CompanyOrchestrator
    from aider.company.schemas import CompanyTask, Deliverable
    from aider.memory import ProjectMemory

    class SecurityDepartment(Department):
        name = "security_app"

        async def process(self, task: CompanyTask) -> Deliverable:
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type="security_scan_result",
                payload={"scan_type": "vuln", "severity": "info", "findings": []},
                status="success",
                metadata={},
            )

    workflow_path = write_workflow(
        tmp_path, tmp_path / "issues.json", tmp_path / "runs"
    )
    workflow = CompanyWorkflow.load(workflow_path)
    memory = ProjectMemory(str(tmp_path / "memory"))
    memory.data["security"] = {
        "last_scan_at": datetime.now(timezone.utc).isoformat(),
        "security_scan_interval_minutes": 1,
        "security_scan_backoff_minutes": 1,
        "security_scan_min_frequency_minutes": 15,
    }
    orchestrator = CompanyOrchestrator(memory)
    orchestrator.register(SecurityDepartment(memory))
    daemon = CompanyDaemon(
        workflow=workflow,
        orchestrator=orchestrator,
        coo=NanobotCOO(orchestrator=orchestrator),
    )

    result = daemon.run_idle_security_check(force=True)

    assert result["status"] == "skipped"
    assert result["reason"] == "security scan safety fuse has not elapsed"


def test_daemon_partial_success_requires_review_status(tmp_path):
    tracker_path = tmp_path / "issues.json"
    runs_path = tmp_path / "runs"
    tracker_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "identifier": "AP-7",
                        "title": "Partial daemon run",
                        "labels": ["aider-plus"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workflow = CompanyWorkflow.load(write_workflow(tmp_path, tracker_path, runs_path))

    def runner(prompt, workspace, issue):
        workspace.joinpath("partial.txt").write_text("partial", encoding="utf-8")
        return {
            "summary": "Implemented with QA follow-ups",
            "changed_files": ["partial.txt"],
            "checks": [{"command": "pytest", "status": "failed"}],
            "qa_result": "failure",
            "review_result": "needs-review",
            "completed_stages": ["engineering", "qa"],
            "failed_stages": ["qa"],
            "partial_success": True,
            "human_review_required": False,
            "pr_url": "https://example.test/pr/7",
        }

    daemon = CompanyDaemon(workflow=workflow, runner=runner)
    proof = daemon.run_once()[0]

    assert proof.partial_success is True
    assert proof.human_review_required is False
    data = json.loads(tracker_path.read_text(encoding="utf-8"))
    raw = data["issues"][0]
    assert raw["status"] == "human_review"
    assert raw["pull_requests"][0]["summary"]
    assert "Partial success: True" in raw["comments"][0]["body"]
    state = json.loads(
        runs_path.joinpath("AP-7", ".aider", "company", "run-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["status"] == "human_review"


def test_daemon_cleanup_prunes_only_terminal_safe_workspaces(tmp_path):
    tracker_path = tmp_path / "issues.json"
    runs_path = tmp_path / "runs"
    workflow_path = write_workflow(tmp_path, tracker_path, runs_path)
    text = workflow_path.read_text(encoding="utf-8")
    text = text.replace(
        "workspace:\n  root:",
        "workspace:\n  cleanup_completed: true\n  max_retained_runs: 1\n  max_age_days: 1\n  root:",
    )
    workflow_path.write_text(text, encoding="utf-8")
    workflow = CompanyWorkflow.load(workflow_path)
    daemon = CompanyDaemon(workflow=workflow)

    old_done = runs_path / "old-done"
    new_done = runs_path / "new-done"
    running = runs_path / "running"
    for path, status, updated in (
        (old_done, "done", "2000-01-01T00:00:00+00:00"),
        (new_done, "done", "2999-01-01T00:00:00+00:00"),
        (running, "running", "2000-01-01T00:00:00+00:00"),
    ):
        state_dir = path / ".aider" / "company"
        state_dir.mkdir(parents=True)
        state_dir.joinpath("run-state.json").write_text(
            json.dumps(
                {
                    "issue_id": path.name,
                    "status": status,
                    "workspace": str(path),
                    "attempts": 1,
                    "updated_at": updated,
                }
            ),
            encoding="utf-8",
        )

    result = daemon.cleanup_workspaces()

    assert str(old_done) in result["removed"]
    assert not old_done.exists()
    assert new_done.exists()
    assert running.exists()


def test_github_status_transitions_use_review_and_failed_labels():
    from aider.company.tracker.github import _labels_for_status, _normalize_status

    labels = {
        "todo": "company:todo",
        "in_progress": "company:in-progress",
        "human_review": "company:review",
        "retry": "company:retry",
        "failed": "company:failed",
        "done": "company:done",
    }

    assert _normalize_status("partial-success") == "human_review"
    assert _normalize_status("failed") == "failed"
    assert _labels_for_status({"bug", "company:todo"}, "human_review", labels) == {
        "bug",
        "company:review",
    }
    assert _labels_for_status({"bug", "company:retry"}, "failed", labels) == {
        "bug",
        "company:failed",
    }


def test_proof_markdown_surfaces_devops_preview_and_rollback_metadata():
    from aider.company.schemas import ProofOfWork

    proof = ProofOfWork(
        issue="AP-ROLLBACK",
        title="Show release metadata",
        workspace="/tmp/ap-rollback",
        summary="Release proof generated.",
        devops_status={
            "status": "success",
            "metadata": {
                "deployment_result": {
                    "dry_run_preview": {
                        "human_summary": "Deploy app:v1 to aws/production; would execute 1 provider command.",
                        "approval_gate": "Approval is required before executing aws/production.",
                        "steps": ["Collect approval", "Capture command logs"],
                    },
                    "rollback_metadata": {
                        "provider": "aws",
                        "environment": "production",
                        "command": "aws deploy create-deployment --revision app:v0",
                        "owner": "Release Captain",
                        "previous_artifact": "app:v0",
                        "current_artifact": "app:v1",
                        "validation_steps": ["Run smoke tests"],
                    },
                }
            },
        },
    )

    markdown = proof.to_markdown()

    assert "### DevOps Dry-run Preview" in markdown
    assert "Deploy app:v1 to aws/production" in markdown
    assert "Approval is required before executing aws/production" in markdown
    assert "### DevOps Rollback Metadata" in markdown
    assert "Owner: Release Captain" in markdown
    assert "Previous artifact: app:v0" in markdown
