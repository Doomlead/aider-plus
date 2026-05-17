"""Linear tracker adapter for Company daemon runs."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from aider.company.tracker import TrackerAdapter, TrackerError, TrackerIssue

_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
_STATUS_QUERY = """
query Issue($id: String!) {
  issue(id: $id) { id identifier title description url state { name type } labels { nodes { name } } }
}
"""
_LIST_QUERY = """
query Issues($filter: IssueFilter) {
  issues(first: 50, filter: $filter) {
    nodes { id identifier title description url state { name type } labels { nodes { name } } }
  }
}
"""
_COMMENT_MUTATION = """
mutation CommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) { success }
}
"""
_STATE_MUTATION = """
mutation IssueUpdate($id: String!, $stateId: String) {
  issueUpdate(id: $id, input: {stateId: $stateId}) { success issue { id identifier title description url state { name type } labels { nodes { name } } } }
}
"""
_STATES_QUERY = """
query WorkflowStates { workflowStates(first: 100) { nodes { id name type } } }
"""


@dataclass(frozen=True)
class _LinearState:
    id: str
    name: str
    type: str


class LinearTrackerAdapter(TrackerAdapter):
    """Tracker adapter backed by Linear issues via the public GraphQL API."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_url: str = "https://api.linear.app/graphql",
        labels: tuple[str, ...] = (),
        status_states: dict[str, str] | None = None,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.token = (token or os.environ.get("LINEAR_API_KEY") or "").strip()
        if not self.token:
            raise TrackerError(
                "Linear tracker requires LINEAR_API_KEY or token config."
            )
        self.api_url = api_url
        self.labels = tuple(labels)
        self.status_states = {k: v for k, v in (status_states or {}).items() if v}
        self.timeout = timeout
        self._client = client
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self._sleep = sleep
        self.retry_count = 0
        self.last_error: str | None = None
        self.retry_events: list[dict[str, Any]] = []
        self._states: list[_LinearState] | None = None

    def list_candidate_issues(self, labels: tuple[str, ...] = ()) -> list[TrackerIssue]:
        required = tuple(labels or self.labels)
        label_filter = (
            {"labels": {"name": {"in": list(required)}}} if required else None
        )
        state_filter = {"state": {"type": {"nin": ["completed", "canceled"]}}}
        filter_arg = (
            {"and": [state_filter, label_filter]} if label_filter else state_filter
        )
        payload = self._graphql(_LIST_QUERY, {"filter": filter_arg})
        nodes = ((payload.get("data") or {}).get("issues") or {}).get("nodes") or []
        return [
            self._issue_from_payload(node) for node in nodes if isinstance(node, dict)
        ]

    def claim_issue(self, issue: TrackerIssue) -> TrackerIssue:
        return self.transition(issue, "in_progress")

    def comment(self, issue: TrackerIssue, body: str) -> None:
        self._graphql(
            _COMMENT_MUTATION, {"issueId": self._linear_id(issue), "body": body}
        )

    def transition(self, issue: TrackerIssue, status: str) -> TrackerIssue:
        state_id = self._state_id_for(status)
        if not state_id:
            return issue
        payload = self._graphql(
            _STATE_MUTATION, {"id": self._linear_id(issue), "stateId": state_id}
        )
        updated = ((payload.get("data") or {}).get("issueUpdate") or {}).get(
            "issue"
        ) or {}
        return self._issue_from_payload(updated) if updated else issue

    def attach_pr(self, issue: TrackerIssue, pr_url: str, **kwargs: Any) -> None:
        proof = kwargs.get("proof")
        if proof is None:
            body = f"Linked pull request: {pr_url}"
        else:
            proof_link = getattr(proof, "markdown_path", None) or "proof-of-work.md"
            body = (
                f"Linked pull request: {pr_url}\n\n"
                f"Proof report: [ProofOfWork Markdown]({proof_link})\n"
                f"Summary: {getattr(proof, 'summary', '') or 'No summary provided.'}"
            )
        self.comment(issue, body)

    def status(self) -> dict[str, Any]:
        return {
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "recent_retries": self.retry_events[-5:],
        }

    def _graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"Authorization": self.token, "Content-Type": "application/json"}
        payload = {"query": query, "variables": variables or {}}
        response: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            client = self._client or httpx.Client(timeout=self.timeout)
            close_client = self._client is None
            try:
                response = client.post(self.api_url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                self.last_error = str(exc)
                if attempt >= self.max_retries:
                    raise TrackerError(f"Linear API request failed: {exc}") from exc
                delay = self.retry_backoff_seconds * (2**attempt)
                self._record_retry(attempt + 1, delay, str(exc))
                self._sleep(delay)
                continue
            finally:
                if close_client:
                    client.close()
            if (
                response.status_code not in _RETRY_STATUS_CODES
                or attempt >= self.max_retries
            ):
                break
            self.last_error = response.text[:500]
            delay = _linear_retry_delay(response, attempt, self.retry_backoff_seconds)
            self._record_retry(
                attempt + 1, delay, self.last_error, response.status_code
            )
            self._sleep(delay)
        if response is None:
            raise TrackerError("Linear API request failed without a response.")
        if response.status_code >= 400:
            raise TrackerError(
                f"Linear API request failed with {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        if data.get("errors"):
            raise TrackerError(f"Linear API returned errors: {data['errors']}")
        self.last_error = None
        return data

    def _record_retry(
        self, attempt: int, delay: float, error: str, status_code: int | None = None
    ) -> None:
        self.retry_count += 1
        event = {"attempt": attempt, "delay_seconds": delay, "error": error}
        if status_code is not None:
            event["status_code"] = status_code
        self.retry_events.append(event)
        del self.retry_events[:-20]

    def _linear_id(self, issue: TrackerIssue) -> str:
        return str(issue.metadata.get("linear_id") or issue.identifier)

    def _states_by_name(self) -> list[_LinearState]:
        if self._states is None:
            payload = self._graphql(_STATES_QUERY)
            nodes = ((payload.get("data") or {}).get("workflowStates") or {}).get(
                "nodes"
            ) or []
            self._states = [
                _LinearState(
                    str(n.get("id")), str(n.get("name", "")), str(n.get("type", ""))
                )
                for n in nodes
                if isinstance(n, dict)
            ]
        return self._states

    def _state_id_for(self, status: str) -> str | None:
        wanted = self.status_states.get(status) or {
            "in_progress": "started",
            "done": "completed",
            "retry": "unstarted",
            "todo": "unstarted",
        }.get(status)
        for state in self._states_by_name():
            if (
                state.id == wanted
                or state.name.lower() == str(wanted).lower()
                or state.type == wanted
            ):
                return state.id
        return None

    def _issue_from_payload(self, raw: dict[str, Any]) -> TrackerIssue:
        labels = tuple(
            str(item.get("name"))
            for item in (((raw.get("labels") or {}).get("nodes")) or [])
            if isinstance(item, dict) and item.get("name")
        )
        state = raw.get("state") or {}
        status = _linear_status(str(state.get("type") or state.get("name") or ""))
        return TrackerIssue(
            identifier=str(raw.get("identifier") or raw.get("id") or ""),
            title=str(raw.get("title") or raw.get("identifier") or ""),
            description=str(raw.get("description") or ""),
            status=status,
            labels=labels,
            url=str(raw.get("url") or ""),
            metadata={"linear_id": raw.get("id"), "linear_state": state},
        )


def _linear_status(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "_")
    if normalized in {"completed", "done", "closed"}:
        return "done"
    if normalized in {"started", "in_progress", "in_progress"}:
        return "in_progress"
    if normalized in {"backlog", "unstarted", "planned", "triage"}:
        return "todo"
    return "retry" if normalized in {"failed", "blocked"} else "todo"


def _linear_retry_delay(
    response: httpx.Response, attempt: int, backoff: float
) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return backoff * (2**attempt)
