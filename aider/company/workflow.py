"""Symphony-inspired workflow configuration for Aider Plus Company daemon runs."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class WorkflowError(ValueError):
    """Raised when a Company daemon workflow cannot be parsed or executed."""


@dataclass(frozen=True)
class TrackerWorkflowConfig:
    kind: str = "local"
    path: str | None = None
    repo: str | None = None
    labels: tuple[str, ...] = ()
    github: dict[str, Any] = field(default_factory=dict)
    linear: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkspaceWorkflowConfig:
    root: str | None = None
    clean: bool = False


@dataclass(frozen=True)
class AgentWorkflowConfig:
    max_concurrent_agents: int = 1
    max_turns: int = 3
    max_attempts: int = 1


@dataclass(frozen=True)
class CompanyWorkflowConfig:
    route: str = "product_to_release"
    require_release_approval: bool = True
    template: str | None = None


@dataclass(frozen=True)
class SecurityWorkflowConfig:
    security_scan_interval_minutes: int = 60
    security_scan_backoff_minutes: int = 240


@dataclass(frozen=True)
class WorkflowHooks:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_seconds: int = 120

    def names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in ("after_create", "before_run", "after_run", "before_remove")
            if getattr(self, name)
        )


@dataclass(frozen=True)
class CompanyWorkflow:
    """Repo-owned policy for tracker-driven Company daemon work."""

    path: Path
    prompt: str
    tracker: TrackerWorkflowConfig = field(default_factory=TrackerWorkflowConfig)
    workspace: WorkspaceWorkflowConfig = field(default_factory=WorkspaceWorkflowConfig)
    agent: AgentWorkflowConfig = field(default_factory=AgentWorkflowConfig)
    company: CompanyWorkflowConfig = field(default_factory=CompanyWorkflowConfig)
    security: SecurityWorkflowConfig = field(default_factory=SecurityWorkflowConfig)
    hooks: WorkflowHooks = field(default_factory=WorkflowHooks)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "CompanyWorkflow":
        workflow_path = Path(path).expanduser().resolve()
        if not workflow_path.exists():
            raise WorkflowError(f"Workflow file not found: {workflow_path}")
        text = workflow_path.read_text(encoding="utf-8")
        config, prompt = _split_front_matter(text)
        return cls.from_dict(workflow_path, config, prompt)

    @classmethod
    def from_dict(
        cls, path: str | Path, config: dict[str, Any], prompt: str
    ) -> "CompanyWorkflow":
        tracker_data = dict(config.get("tracker") or {})
        workspace_data = dict(config.get("workspace") or {})
        agent_data = dict(config.get("agent") or {})
        company_data = dict(config.get("company") or {})
        hooks_data = dict(config.get("hooks") or {})
        security_data = dict(config.get("security") or {})

        max_concurrent = _positive_int(
            agent_data.get("max_concurrent_agents", 1), "agent.max_concurrent_agents"
        )
        max_turns = _positive_int(agent_data.get("max_turns", 3), "agent.max_turns")
        max_attempts = _positive_int(
            agent_data.get("max_attempts", 1), "agent.max_attempts"
        )
        timeout = _positive_int(
            hooks_data.get("timeout_seconds", 120), "hooks.timeout_seconds"
        )

        labels = tracker_data.get("labels") or ()
        if isinstance(labels, str):
            labels = (labels,)

        return cls(
            path=Path(path).expanduser().resolve(),
            prompt=prompt.strip(),
            tracker=TrackerWorkflowConfig(
                kind=str(tracker_data.get("kind", "local") or "local"),
                path=(
                    str(tracker_data.get("path"))
                    if tracker_data.get("path") is not None
                    else None
                ),
                repo=(
                    str(tracker_data.get("repo"))
                    if tracker_data.get("repo") is not None
                    else None
                ),
                labels=tuple(str(label) for label in labels),
                github=(
                    dict(tracker_data.get("github") or {})
                    if isinstance(tracker_data.get("github") or {}, dict)
                    else {}
                ),
                linear=(
                    dict(tracker_data.get("linear") or {})
                    if isinstance(tracker_data.get("linear") or {}, dict)
                    else {}
                ),
            ),
            workspace=WorkspaceWorkflowConfig(
                root=(
                    str(workspace_data.get("root"))
                    if workspace_data.get("root") is not None
                    else None
                ),
                clean=bool(workspace_data.get("clean", False)),
            ),
            agent=AgentWorkflowConfig(
                max_concurrent_agents=max_concurrent,
                max_turns=max_turns,
                max_attempts=max_attempts,
            ),
            company=CompanyWorkflowConfig(
                route=str(company_data.get("route", "product_to_release")),
                require_release_approval=bool(
                    company_data.get("require_release_approval", True)
                ),
                template=(
                    str(company_data.get("template"))
                    if company_data.get("template") is not None
                    else None
                ),
            ),
            security=SecurityWorkflowConfig(
                security_scan_interval_minutes=_positive_int(
                    security_data.get("security_scan_interval_minutes", 60),
                    "security.security_scan_interval_minutes",
                ),
                security_scan_backoff_minutes=_positive_int(
                    security_data.get("security_scan_backoff_minutes", 240),
                    "security.security_scan_backoff_minutes",
                ),
            ),
            hooks=WorkflowHooks(
                after_create=_optional_script(hooks_data.get("after_create")),
                before_run=_optional_script(hooks_data.get("before_run")),
                after_run=_optional_script(hooks_data.get("after_run")),
                before_remove=_optional_script(hooks_data.get("before_remove")),
                timeout_seconds=timeout,
            ),
            raw=config,
        )

    def render_prompt(self, issue: Any) -> str:
        """Render the workflow prompt with a tiny, safe issue placeholder set."""

        mapping = {
            "issue.identifier": getattr(issue, "identifier", ""),
            "issue.title": getattr(issue, "title", ""),
            "issue.description": getattr(issue, "description", ""),
            "issue.url": getattr(issue, "url", ""),
        }
        rendered = self.prompt
        for key, value in mapping.items():
            rendered = rendered.replace("{{ " + key + " }}", str(value))
            rendered = rendered.replace("{{" + key + "}}", str(value))
        return rendered

    def run_hook(
        self,
        name: str,
        *,
        cwd: str | Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        """Run a configured workflow hook inside a workspace."""

        script = getattr(self.hooks, name, None)
        if not script:
            return None
        if name not in {"after_create", "before_run", "after_run", "before_remove"}:
            raise WorkflowError(f"Unknown workflow hook: {name}")
        return subprocess.run(
            script,
            shell=True,
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            timeout=self.hooks.timeout_seconds,
        )


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.DOTALL)
    if not match:
        raise WorkflowError("Workflow front matter starts with --- but is not closed.")
    config = yaml.safe_load(match.group(1)) or {}
    if not isinstance(config, dict):
        raise WorkflowError("Workflow front matter must be a YAML mapping.")
    return config, match.group(2)


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise WorkflowError(f"{name} must be a positive integer.")
    return parsed


def _optional_script(value: Any) -> str | None:
    if value is None:
        return None
    script = str(value).strip()
    return script or None
