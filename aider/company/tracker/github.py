"""GitHub Issues tracker adapter for Company daemon runs."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from aider.company.tracker import (
    TrackerAdapter,
    TrackerError,
    TrackerIssue,
    format_proof_summary,
)

_OPEN_STATES = {"todo", "ready", "open", "retry"}
_IN_PROGRESS_LABELS = {"in_progress", "in-progress", "running", "claimed"}
_DONE_LABELS = {"done", "complete", "completed"}
_CLOSED_STATES = {"done", "closed"}
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
_DEFAULT_STATUS_LABELS = {
    "todo": "todo",
    "in_progress": "in_progress",
    "human_review": "human_review",
    "retry": "retry",
    "failed": "failed",
    "done": "done",
}


@dataclass
class _CachedIssues:
    expires_at: float
    issues: list[TrackerIssue]


class GitHubTrackerAdapter(TrackerAdapter):
    """Tracker adapter backed by GitHub Issues.

    Configuration is intentionally small so daemon workflows can work with a
    normal repository out of the box:

    - ``GITHUB_TOKEN`` for a personal access token.
    - ``GITHUB_REPO`` or ``repo`` for the ``owner/name`` repository slug.
    - ``GITHUB_APP_ID``, ``GITHUB_APP_INSTALLATION_ID``, and either
      ``GITHUB_APP_PRIVATE_KEY`` or ``GITHUB_APP_PRIVATE_KEY_PATH`` for GitHub
      App installation authentication.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        repo: str | None = None,
        api_url: str = "https://api.github.com",
        timeout: float = 20.0,
        client: httpx.Client | None = None,
        app_id: str | None = None,
        app_installation_id: str | None = None,
        app_private_key: str | None = None,
        app_private_key_path: str | None = None,
        status_labels: dict[str, str] | None = None,
        cache_ttl_seconds: int | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        max_retry_after_seconds: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.token = (token or os.environ.get("GITHUB_TOKEN") or "").strip()
        self.repo = (repo or os.environ.get("GITHUB_REPO") or "").strip()
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self.app_id = (app_id or os.environ.get("GITHUB_APP_ID") or "").strip()
        self.app_installation_id = (
            app_installation_id or os.environ.get("GITHUB_APP_INSTALLATION_ID") or ""
        ).strip()
        self.app_private_key = (
            app_private_key or os.environ.get("GITHUB_APP_PRIVATE_KEY") or ""
        )
        self.app_private_key_path = (
            app_private_key_path or os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH") or ""
        ).strip()
        self.status_labels = _normalize_status_labels(status_labels)
        self.cache_ttl_seconds = _cache_ttl(cache_ttl_seconds)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.max_retry_after_seconds = max(0.0, float(max_retry_after_seconds))
        self._sleep = sleep
        self._issue_cache: dict[tuple[str, ...], _CachedIssues] = {}
        self.retry_count = 0
        self.last_error: str | None = None
        self.retry_events: list[dict[str, Any]] = []
        self._installation_token: str | None = None
        self._installation_token_expires_at = 0.0
        if not self._has_auth_config():
            raise TrackerError(
                "GitHub tracker requires GITHUB_TOKEN/token config or GitHub App "
                "credentials (GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, and "
                "GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH)."
            )
        if not self.repo or not _valid_repo(self.repo):
            raise TrackerError(
                "GitHub tracker requires GITHUB_REPO or repo config in owner/repo format."
            )

    def list_candidate_issues(self, labels: tuple[str, ...] = ()) -> list[TrackerIssue]:
        cache_key = tuple(labels)
        cached = self._issue_cache.get(cache_key)
        now = time.time()
        if cached and cached.expires_at > now:
            return list(cached.issues)

        params: dict[str, str] = {
            "state": "open",
            "per_page": "100",
        }
        if labels:
            params["labels"] = ",".join(labels)
        payload = self._request("GET", f"/repos/{self.repo}/issues", params=params)
        if not isinstance(payload, list):
            raise TrackerError("GitHub issues response was not a list.")
        issues: list[TrackerIssue] = []
        for raw in payload:
            if not isinstance(raw, dict) or raw.get("pull_request"):
                continue
            issue = self._issue_from_payload(raw)
            if issue.status in _OPEN_STATES:
                issues.append(issue)
        self._issue_cache[cache_key] = _CachedIssues(
            expires_at=now + self.cache_ttl_seconds,
            issues=list(issues),
        )
        return issues

    def claim_issue(self, issue: TrackerIssue) -> TrackerIssue:
        return self.transition(issue, "in_progress")

    def comment(self, issue: TrackerIssue, body: str) -> None:
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{_issue_number(issue)}/comments",
            json={"body": body},
        )

    def transition(self, issue: TrackerIssue, status: str) -> TrackerIssue:
        normalized = _normalize_status(status)
        number = _issue_number(issue)
        existing_labels = set(issue.labels)
        labels = _labels_for_status(existing_labels, normalized, self.status_labels)
        body: dict[str, Any] = {"labels": sorted(labels)}
        if normalized in _CLOSED_STATES:
            body["state"] = "closed"
        elif issue.metadata.get("github_state") == "closed":
            body["state"] = "open"
        payload = self._request(
            "PATCH", f"/repos/{self.repo}/issues/{number}", json=body
        )
        self._clear_issue_cache()
        if not isinstance(payload, dict):
            raise TrackerError("GitHub issue update response was not an object.")
        return self._issue_from_payload(payload)

    def attach_pr(self, issue: TrackerIssue, pr_url: str, **kwargs: Any) -> None:
        proof = kwargs.get("proof")
        if proof is None:
            body = f"Linked pull request: {pr_url}"
        else:
            body = (
                "Linked pull request with daemon proof-of-work.\n\n"
                + format_proof_summary(proof, pr_url)
            )
        self.comment(issue, body)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._auth_token()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aider-plus-company-daemon",
        }
        return self._send(method, path, headers=headers, **kwargs)

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.api_url}{path}"
        last_response: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            client = self._client or _retry_client(self.timeout, self.max_retries)
            close_client = self._client is None
            try:
                response = client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                self.last_error = str(exc)
                if attempt < self.max_retries:
                    delay = self.retry_backoff_seconds * (2**attempt)
                    self._record_retry(method, path, attempt + 1, delay, str(exc))
                    self._sleep(delay)
                    continue
                raise TrackerError(f"GitHub API request failed: {exc}") from exc
            finally:
                if close_client:
                    client.close()

            last_response = response
            if not _should_retry_response(response) or attempt >= self.max_retries:
                break
            delay = _retry_delay(response, attempt, self)
            self.last_error = _github_error_message(response)
            self._record_retry(
                method, path, attempt + 1, delay, self.last_error, response.status_code
            )
            self._sleep(delay)

        if last_response is None:
            raise TrackerError(f"GitHub API {method} {path} failed without a response.")
        if last_response.status_code >= 400:
            message = _github_error_message(last_response)
            raise TrackerError(
                f"GitHub API {method} {path} failed with "
                f"{last_response.status_code}: {message}"
            )
        if last_response.status_code == 204 or not last_response.content:
            return None
        self.last_error = None
        return last_response.json()

    def _record_retry(
        self,
        method: str,
        path: str,
        attempt: int,
        delay: float,
        error: str,
        status_code: int | None = None,
    ) -> None:
        self.retry_count += 1
        event = {
            "method": method,
            "path": path,
            "attempt": attempt,
            "delay_seconds": delay,
            "error": error,
        }
        if status_code is not None:
            event["status_code"] = status_code
        self.retry_events.append(event)
        del self.retry_events[:-20]

    def status(self) -> dict[str, Any]:
        return {
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "recent_retries": list(self.retry_events[-5:]),
        }

    def _auth_token(self) -> str:
        if self.token:
            return self.token
        now = time.time()
        if self._installation_token and self._installation_token_expires_at > now + 60:
            return self._installation_token
        jwt_token = self._app_jwt()
        payload = self._send(
            "POST",
            f"/app/installations/{self.app_installation_id}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {jwt_token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "aider-plus-company-daemon",
            },
        )
        if not isinstance(payload, dict) or not payload.get("token"):
            raise TrackerError("GitHub App installation token response was invalid.")
        self._installation_token = str(payload["token"])
        self._installation_token_expires_at = _parse_github_time(
            payload.get("expires_at"), default_seconds=540
        )
        return self._installation_token

    def _app_jwt(self) -> str:
        if not (self.app_id and self.app_installation_id):
            raise TrackerError(
                "GitHub App authentication requires app_id and app_installation_id."
            )
        private_key = self.app_private_key or _read_private_key(
            self.app_private_key_path
        )
        if not private_key:
            raise TrackerError(
                "GitHub App authentication requires app_private_key or app_private_key_path."
            )
        private_key = private_key.replace("\\n", "\n")

        import importlib.util

        if importlib.util.find_spec("cryptography") is None:
            raise TrackerError(
                "GitHub App authentication requires the optional cryptography package."
            )
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {"iat": now - 60, "exp": now + 540, "iss": self.app_id}
        signing_input = (
            _base64url_json(header) + "." + _base64url_json(payload)
        ).encode("ascii")
        key = serialization.load_pem_private_key(private_key.encode(), password=None)
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return signing_input.decode("ascii") + "." + _base64url(signature)

    def _has_auth_config(self) -> bool:
        return bool(
            self.token
            or (
                self.app_id
                and self.app_installation_id
                and (self.app_private_key or self.app_private_key_path)
            )
        )

    def _clear_issue_cache(self) -> None:
        self._issue_cache.clear()

    def _issue_from_payload(self, raw: dict[str, Any]) -> TrackerIssue:
        labels = tuple(
            str(label.get("name", ""))
            for label in raw.get("labels", ())
            if isinstance(label, dict) and label.get("name")
        )
        state = _status_from_github(
            str(raw.get("state") or "open"), labels, self.status_labels
        )
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


def _retry_client(timeout: float, retries: int) -> httpx.Client:
    transport = httpx.HTTPTransport(retries=retries)
    return httpx.Client(timeout=timeout, transport=transport)


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
        "closed": "done",
        "complete": "done",
        "completed": "done",
        "open": "todo",
        "ready": "todo",
        "triage": "todo",
        "backlog": "todo",
        "blocked": "retry",
        "needs_review": "human_review",
        "review": "human_review",
        "partial_success": "human_review",
    }
    return aliases.get(normalized, normalized or "todo")


def _status_from_github(
    github_state: str, labels: tuple[str, ...], status_labels: dict[str, str]
) -> str:
    label_set = {_label_key(label) for label in labels}
    if github_state == "closed" or _label_key(status_labels["done"]) in label_set:
        return "done"
    if _label_key(status_labels["in_progress"]) in label_set or label_set & {
        _label_key(label) for label in _IN_PROGRESS_LABELS
    }:
        return "in_progress"
    if _label_key(status_labels["human_review"]) in label_set or label_set & {
        "needs_review",
        "human_review",
    }:
        return "human_review"
    if _label_key(status_labels["failed"]) in label_set or "failed" in label_set:
        return "failed"
    if _label_key(status_labels["retry"]) in label_set or label_set & {
        "blocked",
        "needs_retry",
    }:
        return "retry"
    return "todo"


def _labels_for_status(
    existing: set[str], status: str, status_labels: dict[str, str]
) -> set[str]:
    known_status_labels = {_label_key(label) for label in status_labels.values()}
    known_status_labels.update(
        {
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
            "blocked",
            "needs_retry",
            "failed",
            "needs_review",
            "human_review",
        }
    )
    cleaned = {
        label for label in existing if _label_key(label) not in known_status_labels
    }
    if status == "in_progress":
        cleaned.add(status_labels["in_progress"])
    elif status == "human_review":
        cleaned.add(status_labels["human_review"])
    elif status == "failed":
        cleaned.add(status_labels["failed"])
    elif status == "retry":
        cleaned.add(status_labels["retry"])
    elif status in _CLOSED_STATES:
        cleaned.add(status_labels["done"])
    elif status in {"todo", "open", "ready"}:
        cleaned.add(status_labels["todo"])
    else:
        cleaned.add(status)
    return cleaned


def _normalize_status_labels(status_labels: dict[str, str] | None) -> dict[str, str]:
    labels = dict(_DEFAULT_STATUS_LABELS)
    for key, value in (status_labels or {}).items():
        normalized = _normalize_status(str(key))
        if normalized in labels and str(value).strip():
            labels[normalized] = str(value).strip()
    return labels


def _label_key(label: str) -> str:
    return label.lower().replace(" ", "_").replace("-", "_")


def _should_retry_response(response: httpx.Response) -> bool:
    if response.status_code in _RETRY_STATUS_CODES:
        return True
    return response.status_code == 403 and (
        bool(response.headers.get("retry-after"))
        or response.headers.get("x-ratelimit-remaining") == "0"
    )


def _retry_delay(
    response: httpx.Response, attempt: int, adapter: GitHubTrackerAdapter
) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return min(float(retry_after), adapter.max_retry_after_seconds)
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    if reset:
        try:
            reset_delay = max(0.0, float(reset) - time.time())
            return min(reset_delay, adapter.max_retry_after_seconds)
        except ValueError:
            pass
    return adapter.retry_backoff_seconds * (2**attempt)


def _github_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(payload, dict):
        return str(payload.get("message") or payload)[:500]
    return str(payload)[:500]


def _cache_ttl(value: int | None) -> int:
    if value is None:
        raw = os.environ.get("GITHUB_ISSUES_CACHE_TTL_SECONDS", "300")
        try:
            value = int(raw)
        except ValueError:
            value = 300
    return max(0, min(int(value), 600))


def _read_private_key(path: str) -> str:
    if not path:
        return ""
    return Path(path).expanduser().read_text(encoding="utf-8")


def _base64url_json(payload: dict[str, Any]) -> str:
    return _base64url(json.dumps(payload, separators=(",", ":")).encode())


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _parse_github_time(value: Any, *, default_seconds: int) -> float:
    if not value:
        return time.time() + default_seconds
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return time.time() + default_seconds
    return parsed.astimezone(timezone.utc).timestamp()
