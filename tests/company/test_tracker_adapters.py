import json

import httpx
import pytest

from aider.company.cli import parse_company_cli
from aider.company.tracker import TrackerError, create_tracker_adapter
from aider.company.tracker.github import GitHubTrackerAdapter


def test_create_tracker_adapter_returns_github_adapter(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    adapter = create_tracker_adapter({"type": "github", "repo": "owner/repo"})

    assert isinstance(adapter, GitHubTrackerAdapter)
    assert adapter.repo == "owner/repo"


def test_github_tracker_lists_claims_comments_transitions_and_attaches_pr():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer token"
        if request.method == "GET" and request.url.path.endswith("/issues"):
            assert request.url.params["labels"] == "aider-plus"
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1001,
                        "number": 7,
                        "title": "Build GitHub tracker",
                        "body": "Use real issues.",
                        "state": "open",
                        "html_url": "https://github.com/owner/repo/issues/7",
                        "labels": [{"name": "aider-plus"}],
                    },
                    {
                        "id": 1002,
                        "number": 8,
                        "title": "Ignore PRs",
                        "state": "open",
                        "pull_request": {"url": "https://api.github.test/prs/8"},
                        "labels": [{"name": "aider-plus"}],
                    },
                ],
            )
        if request.method == "PATCH" and request.url.path.endswith("/issues/7"):
            body = json.loads(request.content.decode())
            labels = body["labels"]
            if "in_progress" in labels:
                status_label = "in_progress"
                state = "open"
            elif "done" in labels:
                status_label = "done"
                state = body["state"]
            else:
                status_label = labels[-1]
                state = "open"
            return httpx.Response(
                200,
                json={
                    "id": 1001,
                    "number": 7,
                    "title": "Build GitHub tracker",
                    "body": "Use real issues.",
                    "state": state,
                    "html_url": "https://github.com/owner/repo/issues/7",
                    "labels": [{"name": "aider-plus"}, {"name": status_label}],
                },
            )
        if request.method == "POST" and request.url.path.endswith("/issues/7/comments"):
            return httpx.Response(201, json={"id": 5001})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.test")
    tracker = GitHubTrackerAdapter(
        token="token",
        repo="owner/repo",
        api_url="https://api.github.test",
        client=client,
    )

    issues = tracker.list_candidates(("aider-plus",))
    assert [issue.identifier for issue in issues] == ["7"]
    assert issues[0].status == "todo"

    claimed = tracker.claim(issues[0])
    assert claimed.status == "in_progress"

    tracker.comment(claimed, "Working on it")
    tracker.attach_pr(claimed, "https://github.com/owner/repo/pull/9")

    done = tracker.transition(claimed, "done")
    assert done.status == "done"
    assert any(
        request.method == "PATCH"
        and json.loads(request.content.decode()).get("state") == "closed"
        for request in requests
    )
    assert sum(1 for request in requests if request.method == "POST") == 2


def test_github_tracker_requires_repo_and_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)

    with pytest.raises(TrackerError, match="GITHUB_TOKEN"):
        GitHubTrackerAdapter()

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    with pytest.raises(TrackerError, match="owner/repo"):
        GitHubTrackerAdapter(repo="not-a-slug")


def test_company_daemon_cli_accepts_github_tracker_and_repo():
    command, aider_args = parse_company_cli(
        [
            "company",
            "daemon",
            "--workflow",
            "AIDER_WORKFLOW.md",
            "--tracker",
            "github",
            "--repo",
            "owner/repo",
            "--once",
        ]
    )

    assert aider_args == []
    assert command is not None
    assert command.tracker_type == "github"
    assert command.repo == "owner/repo"
    assert command.once is True
