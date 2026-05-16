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
  template: cli-tool
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
    assert "Product template: CLI tool (cli-tool)" in prompt


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
