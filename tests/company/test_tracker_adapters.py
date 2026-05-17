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


def test_github_tracker_caches_issue_lists_briefly():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1001,
                    "number": 7,
                    "title": "Cached issue",
                    "body": "",
                    "state": "open",
                    "labels": [{"name": "aider-plus"}],
                }
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tracker = GitHubTrackerAdapter(
        token="token",
        repo="owner/repo",
        client=client,
        cache_ttl_seconds=300,
    )

    assert tracker.list_candidate_issues(("aider-plus",))[0].identifier == "7"
    assert tracker.list_candidate_issues(("aider-plus",))[0].identifier == "7"
    assert calls == 1


def test_github_tracker_retries_rate_limited_requests():
    calls = 0
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "2"},
                json={"message": "rate limited"},
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1001,
                    "number": 7,
                    "title": "Retried issue",
                    "body": "",
                    "state": "open",
                    "labels": [{"name": "aider-plus"}],
                }
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tracker = GitHubTrackerAdapter(
        token="token",
        repo="owner/repo",
        client=client,
        max_retries=1,
        sleep=sleeps.append,
    )

    assert tracker.list_candidate_issues(("aider-plus",))[0].title == "Retried issue"
    assert calls == 2
    assert sleeps == [2.0]


def test_github_tracker_supports_github_app_installation_tokens(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/app/installations/99/access_tokens"):
            assert request.headers["Authorization"] == "Bearer app-jwt"
            return httpx.Response(
                201,
                json={"token": "installation-token", "expires_at": "2999-01-01T00:00:00Z"},
            )
        assert request.headers["Authorization"] == "Bearer installation-token"
        return httpx.Response(200, json=[])

    monkeypatch.setattr(GitHubTrackerAdapter, "_app_jwt", lambda self: "app-jwt")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    tracker = GitHubTrackerAdapter(
        repo="owner/repo",
        app_id="123",
        app_installation_id="99",
        app_private_key="fake-key",
        client=client,
    )

    assert tracker.list_candidate_issues() == []
    assert tracker.list_candidate_issues(("aider-plus",)) == []
    token_requests = [
        request for request in requests if request.url.path.endswith("access_tokens")
    ]
    assert len(token_requests) == 1


def test_github_tracker_uses_custom_status_label_mapping():
    patch_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1001,
                        "number": 7,
                        "title": "Custom labels",
                        "body": "",
                        "state": "open",
                        "labels": [{"name": "company:todo"}],
                    }
                ],
            )
        body = json.loads(request.content.decode())
        patch_bodies.append(body)
        return httpx.Response(
            200,
            json={
                "id": 1001,
                "number": 7,
                "title": "Custom labels",
                "body": "",
                "state": body.get("state", "open"),
                "labels": [{"name": label} for label in body["labels"]],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tracker = GitHubTrackerAdapter(
        token="token",
        repo="owner/repo",
        client=client,
        status_labels={
            "todo": "company:todo",
            "in_progress": "company:running",
            "done": "company:done",
        },
    )

    issue = tracker.list_candidate_issues()[0]
    assert issue.status == "todo"
    tracker.transition(issue, "done")
    assert patch_bodies[-1]["labels"] == ["company:done"]


def test_workflow_parses_github_tracker_section(tmp_path):
    from aider.company.workflow import CompanyWorkflow

    workflow_path = tmp_path / "AIDER_WORKFLOW.md"
    workflow_path.write_text(
        """---
tracker:
  kind: github
  repo: owner/repo
  labels: [aider-plus]
  github:
    cache_ttl_seconds: 300
    max_retries: 2
    labels:
      todo: company:todo
      in_progress: company:in-progress
      retry: company:retry
      done: company:done
---
Work on {{ issue.identifier }}.
""",
        encoding="utf-8",
    )

    workflow = CompanyWorkflow.load(workflow_path)

    assert workflow.tracker.kind == "github"
    assert workflow.tracker.repo == "owner/repo"
    assert workflow.tracker.github["cache_ttl_seconds"] == 300
    assert workflow.tracker.github["labels"]["in_progress"] == "company:in-progress"
