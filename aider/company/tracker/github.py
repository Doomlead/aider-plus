"""GitHub Issues tracker adapter for Company daemon runs."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from aider.company.tracker import TrackerAdapter, TrackerError, TrackerIssue


_OPEN_STATES = {"todo", "ready", "open", "retry"}
_IN_PROGRESS_LABELS = {"in_progress", "in-progress", "running", "claimed"}
_DONE_LABELS = {"done", "complete", "completed"}
_CLOSED_STATES = {"done", "closed"}


class GitHubTrackerAdapter(TrackerAdapter):
    """Tracker adapter backed by GitHub Issues.

    Configuration is intentionally small so daemon workflows can work with a
    normal repository out of the box:

    - ``GITHUB_TOKEN`` for authentication.
    - ``GITHUB_REPO`` or ``repo`` for the ``owner/name`` repository slug.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        repo: str | None = None,
        api_url: str = "https://api.github.com",
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ):
        self.token = (token or os.environ.get("GITHUB_TOKEN") or "").strip()
        self.repo = (repo or os.environ.get("GITHUB_REPO") or "").strip()
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        if not self.token:
            raise TrackerError("GitHub tracker requires GITHUB_TOKEN or token config.")
        if not self.repo or not _valid_repo(self.repo):
            raise TrackerError(
                "GitHub tracker requires GITHUB_REPO or repo config in owner/repo format."
            )

    def list_candidate_issues(self, labels: tuple[str, ...] = ()) -> list[TrackerIssue]:
        params: dict[str, str] = {
            "state": "open",
            "per_page": "100",
        }
        if labels:
            params["labels"] = ",".join(labels)
        payload = self._request("GET", "/issues", params=params)
        if not isinstance(payload, list):
            raise TrackerError("GitHub issues response was not a list.")
        issues: list[TrackerIssue] = []
        for raw in payload:
            if not isinstance(raw, dict) or raw.get("pull_request"):
                continue
            issue = self._issue_from_payload(raw)
            if issue.status in _OPEN_STATES:
                issues.append(issue)
        return issues

    def claim_issue(self, issue: TrackerIssue) -> TrackerIssue:
        return self.transition(issue, "in_progress")

    def comment(self, issue: TrackerIssue, body: str) -> None:
        self._request(
            "POST",
            f"/issues/{_issue_number(issue)}/comments",
            json={"body": body},
        )

    def transition(self, issue: TrackerIssue, status: str) -> TrackerIssue:
        normalized = _normalize_status(status)
        number = _issue_number(issue)
        existing_labels = set(issue.labels)
        labels = _labels_for_status(existing_labels, normalized)
        body: dict[str, Any] = {"labels": sorted(labels)}
        if normalized in _CLOSED_STATES:
            body["state"] = "closed"
        elif issue.metadata.get("github_state") == "closed":
            body["state"] = "open"
        payload = self._request("PATCH", f"/issues/{number}", json=body)
        if not isinstance(payload, dict):
            raise TrackerError("GitHub issue update response was not an object.")
        return self._issue_from_payload(payload)

    def attach_pr(self, issue: TrackerIssue, pr_url: str) -> None:
        body = f"Linked pull request: {pr_url}"
        self.comment(issue, body)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.api_url}/repos/{self.repo}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aider-plus-company-daemon",
        }
        client = self._client or httpx.Client(timeout=self.timeout)
        close_client = self._client is None
        try:
            response = client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise TrackerError(f"GitHub API request failed: {exc}") from exc
        finally:
            if close_client:
                client.close()
        if response.status_code >= 400:
            message = _github_error_message(response)
            raise TrackerError(
                f"GitHub API {method} {path} failed with {response.status_code}: {message}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    @staticmethod
    def _issue_from_payload(raw: dict[str, Any]) -> TrackerIssue:
        labels = tuple(
            str(label.get("name", ""))
            for label in raw.get("labels", ())
            if isinstance(label, dict) and label.get("name")
        )
        state = _status_from_github(str(raw.get("state") or "open"), labels)
        number = str(raw.get("number") or raw.get("id") or "").strip()
        if not number:
            raise TrackerError("GitHub issue payload is missing a number/id.")
        return TrackerIssue(
            identifier=number,
            title=str(raw.get("title") or number),
            description=str(raw.get("body") or ""),
            status=state,
            labels=labels,
            url=str(raw.get("html_url") or raw.get("url") or ""),
            metadata={
                "github_id": raw.get("id"),
                "github_number": raw.get("number"),
                "github_state": raw.get("state"),
                "github_node_id": raw.get("node_id"),
            },
        )


def _valid_repo(repo: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo))


def _issue_number(issue: TrackerIssue) -> str:
    number = issue.metadata.get("github_number") or issue.identifier
    text = str(number).strip().lstrip("#")
    if not text.isdigit():
        raise TrackerError(
            f"GitHub issue identifier must be a number: {issue.identifier}"
        )
    return text


def _normalize_status(status: str) -> str:
    normalized = status.strip().lower().replace("-", "_")
    aliases = {
        "running": "in_progress",
        "claimed": "in_progress",
        "human_review": "in_progress",
        "failed": "retry",
        "closed": "done",
        "complete": "done",
        "completed": "done",
        "open": "todo",
        "ready": "todo",
    }
    return aliases.get(normalized, normalized or "todo")


def _status_from_github(github_state: str, labels: tuple[str, ...]) -> str:
    label_set = {label.lower().replace(" ", "_") for label in labels}
    if github_state == "closed" or label_set & _DONE_LABELS:
        return "done"
    if label_set & _IN_PROGRESS_LABELS:
        return "in_progress"
    if "retry" in label_set:
        return "retry"
    return "todo"


def _labels_for_status(existing: set[str], status: str) -> set[str]:
    cleaned = {
        label
        for label in existing
        if label.lower().replace(" ", "_").replace("-", "_")
        not in {
            "todo",
            "ready",
            "open",
            "retry",
            "running",
            "claimed",
            "in_progress",
            "done",
            "complete",
            "completed",
        }
    }
    if status == "in_progress":
        cleaned.add("in_progress")
    elif status == "retry":
        cleaned.add("retry")
    elif status in _CLOSED_STATES:
        cleaned.add("done")
    elif status in {"todo", "open", "ready"}:
        cleaned.add("todo")
    else:
        cleaned.add(status)
    return cleaned


def _github_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(payload, dict):
        return str(payload.get("message") or payload)[:500]
    return str(payload)[:500]
