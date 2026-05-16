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


class TrackerAdapter(ABC):
    """Abstract issue/control-plane adapter used by the Company daemon."""

    @abstractmethod
    def list_candidate_issues(
        self, labels: tuple[str, ...] = ()
    ) -> list[TrackerIssue]: ...

    @abstractmethod
    def claim_issue(self, issue: TrackerIssue) -> TrackerIssue: ...

    @abstractmethod
    def comment(self, issue: TrackerIssue, body: str) -> None: ...

    @abstractmethod
    def transition(self, issue: TrackerIssue, status: str) -> TrackerIssue: ...

    @abstractmethod
    def attach_pr(self, issue: TrackerIssue, pr_url: str) -> None: ...


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

    def attach_pr(self, issue: TrackerIssue, pr_url: str) -> None:
        data = self._read()
        now = _utc_now()
        for raw in data.get("issues", []):
            if _issue_id(raw) == issue.identifier:
                prs = raw.setdefault("pull_requests", [])
                prs.append({"created_at": now, "url": pr_url})
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
