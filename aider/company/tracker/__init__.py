"""Tracker adapters for Symphony-inspired Company daemon runs."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TrackerError(ValueError):
    """Raised when a tracker adapter cannot complete an operation."""


@dataclass(frozen=True)
class TrackerIssue:
    identifier: str
    title: str
    description: str = ""
    status: str = "todo"
    labels: tuple[str, ...] = ()
    url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrackerIssue":
        identifier = str(data.get("identifier") or data.get("id") or "").strip()
        if not identifier:
            raise TrackerError("Tracker issue is missing an identifier/id.")
        labels = data.get("labels") or ()
        if isinstance(labels, str):
            labels = (labels,)
        return cls(
            identifier=identifier,
            title=str(data.get("title") or identifier),
            description=str(data.get("description") or data.get("body") or ""),
            status=str(data.get("status") or "todo"),
            labels=tuple(str(label) for label in labels),
            url=str(data.get("url") or ""),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "labels": list(self.labels),
            "url": self.url,
            "metadata": self.metadata,
        }


def format_proof_summary(proof: Any, pr_url: str | None = None) -> str:
    """Build a compact proof-of-work summary for tracker comments/attachments."""

    proof_link = getattr(proof, "markdown_path", None) or (
        f"{getattr(proof, 'workspace', '')}/.aider/company/proof-of-work.md"
        if getattr(proof, "workspace", "")
        else "proof-of-work.md"
    )
    checks = getattr(proof, "checks", ()) or ()
    check_lines = []
    for check in checks:
        if isinstance(check, dict):
            command = check.get("command") or check.get("name") or "check"
            status = check.get("status") or check.get("result") or "unknown"
            check_lines.append(f"{command}: {status}")
        else:
            check_lines.append(str(check))
    changed_files = getattr(proof, "changed_files", ()) or ()
    risk_notes = getattr(proof, "risk_notes", ()) or ()
    lines = []
    if pr_url:
        lines.append(f"Pull request: {pr_url}")
    lines.extend(
        [
            f"Proof report: [ProofOfWork Markdown]({proof_link})",
            f"Summary: {getattr(proof, 'summary', '') or 'No summary provided.'}",
            f"QA: {getattr(proof, 'qa_result', 'not-run')}",
            f"Review: {getattr(proof, 'review_result', 'not-run')}",
            f"Partial success: {getattr(proof, 'partial_success', False)}",
            f"Human review required: {getattr(proof, 'human_review_required', True)}",
            f"Completed stages: {', '.join(getattr(proof, 'completed_stages', ()) or ()) or 'none'}",
            f"Failed stages: {', '.join(getattr(proof, 'failed_stages', ()) or ()) or 'none'}",
            f"Changed files: {', '.join(str(item) for item in changed_files[:8]) or 'none'}",
            f"Checks: {', '.join(check_lines[:8]) or 'none'}",
            f"Risks / follow-ups: {', '.join(str(item) for item in risk_notes[:5]) or 'none'}",
        ]
    )
    return "\n".join(lines)


class TrackerAdapter(ABC):
    """Abstract issue/control-plane adapter used by the Company daemon."""

    def list_candidates(self, labels: tuple[str, ...] = ()) -> list[TrackerIssue]:
        """Compatibility alias for the common tracker adapter interface."""

        return self.list_candidate_issues(labels)

    @abstractmethod
    def list_candidate_issues(
        self, labels: tuple[str, ...] = ()
    ) -> list[TrackerIssue]: ...

    def claim(self, issue: TrackerIssue) -> TrackerIssue:
        """Compatibility alias for the common tracker adapter interface."""

        return self.claim_issue(issue)

    @abstractmethod
    def claim_issue(self, issue: TrackerIssue) -> TrackerIssue: ...

    @abstractmethod
    def comment(self, issue: TrackerIssue, body: str) -> None: ...

    @abstractmethod
    def transition(self, issue: TrackerIssue, status: str) -> TrackerIssue: ...

    @abstractmethod
    def attach_pr(self, issue: TrackerIssue, pr_url: str, **kwargs: Any) -> None: ...


class LocalJsonTrackerAdapter(TrackerAdapter):
    """A deterministic JSON-file tracker for local queues and tests."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()

    def list_candidate_issues(self, labels: tuple[str, ...] = ()) -> list[TrackerIssue]:
        data = self._read()
        required = set(labels)
        issues: list[TrackerIssue] = []
        for raw in data.get("issues", []):
            issue = TrackerIssue.from_dict(raw)
            if issue.status not in {"todo", "ready", "open", "retry"}:
                continue
            if required and not required.issubset(set(issue.labels)):
                continue
            issues.append(issue)
        return issues

    def claim_issue(self, issue: TrackerIssue) -> TrackerIssue:
        return self.transition(issue, "running")

    def comment(self, issue: TrackerIssue, body: str) -> None:
        data = self._read()
        now = _utc_now()
        for raw in data.get("issues", []):
            if _issue_id(raw) == issue.identifier:
                comments = raw.setdefault("comments", [])
                comments.append({"created_at": now, "body": body})
                raw["updated_at"] = now
                self._write(data)
                return
        raise TrackerError(f"Issue not found: {issue.identifier}")

    def transition(self, issue: TrackerIssue, status: str) -> TrackerIssue:
        data = self._read()
        now = _utc_now()
        for raw in data.get("issues", []):
            if _issue_id(raw) == issue.identifier:
                raw["status"] = status
                raw["updated_at"] = now
                self._write(data)
                updated = dict(raw)
                return TrackerIssue.from_dict(updated)
        raise TrackerError(f"Issue not found: {issue.identifier}")

    def attach_pr(self, issue: TrackerIssue, pr_url: str, **kwargs: Any) -> None:
        data = self._read()
        now = _utc_now()
        for raw in data.get("issues", []):
            if _issue_id(raw) == issue.identifier:
                prs = raw.setdefault("pull_requests", [])
                proof = kwargs.get("proof")
                prs.append(
                    {
                        "created_at": now,
                        "url": pr_url,
                        "proof": proof.to_dict() if hasattr(proof, "to_dict") else None,
                        "summary": (
                            format_proof_summary(proof, pr_url)
                            if proof is not None
                            else ""
                        ),
                    }
                )
                if proof is not None:
                    comments = raw.setdefault("comments", [])
                    comments.append(
                        {"created_at": now, "body": format_proof_summary(proof, pr_url)}
                    )
                raw["updated_at"] = now
                self._write(data)
                return
        raise TrackerError(f"Issue not found: {issue.identifier}")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "issues": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            data = {"version": 1, "issues": data}
        if not isinstance(data, dict) or not isinstance(data.get("issues", []), list):
            raise TrackerError(
                "Local tracker JSON must be a mapping with an issues list."
            )
        data.setdefault("version", 1)
        data.setdefault("issues", [])
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _issue_id(data: dict[str, Any]) -> str:
    return str(data.get("identifier") or data.get("id") or "")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_tracker_adapter(config: Any) -> TrackerAdapter:
    """Create a tracker adapter from workflow/CLI configuration."""

    if isinstance(config, dict):
        github = dict(config.get("github") or {})
        linear = dict(config.get("linear") or {})
        kind = str(config.get("type") or config.get("kind") or "local")
        path = config.get("path")
        repo = config.get("repo")
        token = config.get("token")
        tracker_labels = config.get("labels") or ()
        api_url = str(
            config.get("api_url") or github.get("api_url") or "https://api.github.com"
        )
    else:
        github = dict(getattr(config, "github", None) or {})
        linear = dict(getattr(config, "linear", None) or {})
        kind = str(
            getattr(config, "type", None) or getattr(config, "kind", None) or "local"
        )
        path = getattr(config, "path", None)
        repo = getattr(config, "repo", None)
        token = getattr(config, "token", None)
        tracker_labels = getattr(config, "labels", ()) or ()
        api_url = str(
            getattr(config, "api_url", None)
            or github.get("api_url")
            or "https://api.github.com"
        )

    normalized = kind.strip().lower()
    if normalized == "local":
        if not path:
            raise TrackerError("Local tracker workflows require tracker.path.")
        return LocalJsonTrackerAdapter(path)
    if normalized == "github":
        from aider.company.tracker.github import GitHubTrackerAdapter

        auth = (
            dict(github.get("auth") or {})
            if isinstance(github.get("auth") or {}, dict)
            else {}
        )
        labels = (
            dict(github.get("labels") or {})
            if isinstance(github.get("labels") or {}, dict)
            else {}
        )
        return GitHubTrackerAdapter(
            token=token or auth.get("token"),
            repo=repo,
            api_url=api_url,
            app_id=auth.get("app_id"),
            app_installation_id=auth.get("installation_id"),
            app_private_key=auth.get("private_key"),
            app_private_key_path=auth.get("private_key_path"),
            status_labels=labels,
            cache_ttl_seconds=github.get("cache_ttl_seconds"),
            max_retries=int(github.get("max_retries", 2) or 0),
            retry_backoff_seconds=float(github.get("retry_backoff_seconds", 1.0) or 0),
        )
    if normalized == "linear":
        from aider.company.tracker.linear import LinearTrackerAdapter

        auth = (
            dict(linear.get("auth") or {})
            if isinstance(linear.get("auth") or {}, dict)
            else {}
        )
        states = (
            dict(linear.get("states") or {})
            if isinstance(linear.get("states") or {}, dict)
            else {}
        )
        return LinearTrackerAdapter(
            token=token or auth.get("token"),
            api_url=str(linear.get("api_url") or "https://api.linear.app/graphql"),
            labels=tuple(
                str(label) for label in (linear.get("labels") or tracker_labels)
            ),
            status_states=states,
            max_retries=int(linear.get("max_retries", 2) or 0),
            retry_backoff_seconds=float(linear.get("retry_backoff_seconds", 1.0) or 0),
        )
    raise TrackerError(
        f"Unsupported tracker kind: {kind}. Supported: local, github, linear."
    )
