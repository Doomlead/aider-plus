"""Guided first-run setup for Aider Plus Company Mode."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from aider.company.templates import DEFAULT_TEMPLATE_KEY, get_template, list_templates
from aider.company.warehouse import WarehouseManager, default_warehouse_path

DEPARTMENTS = ("product", "ux", "engineering", "reviewer", "qa", "devops")
ONBOARDING_STATE = Path(".aider") / "company" / "onboarding.json"
WORKFLOW_GUIDE = "AIDER_WORKFLOW.md"


@dataclass(frozen=True)
class CompanyOnboardingResult:
    """Summary of files and choices produced by Company onboarding."""

    warehouse_path: str
    template: str
    github_repo: str = ""
    github_token_configured: bool = False
    mcp_enabled: bool = False
    config_path: str = ""
    workflow_guide_path: str = ""
    daemon_workflow_path: str = ""
    model_preferences: dict[str, dict[str, object]] = field(default_factory=dict)


class CompanyOnboarding:
    """Interactive Company Mode setup flow.

    The class accepts injectable prompt/output callables so CLIs, GUIs, and tests can
    reuse the same setup helpers without depending on a specific terminal UI.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        input_func: Callable[[str], str] | None = None,
        output_func: Callable[[str], None] | None = None,
        defaults: Mapping[str, object] | None = None,
    ) -> None:
        self.root = Path(root or Path.cwd()).expanduser().resolve()
        self.input_func = input_func or input
        self.output_func = output_func or print
        self.defaults = dict(defaults or {})

    @staticmethod
    def state_path(root: str | Path | None = None) -> Path:
        return Path(root or Path.cwd()).expanduser().resolve() / ONBOARDING_STATE

    @staticmethod
    def has_completed(root: str | Path | None = None) -> bool:
        return CompanyOnboarding.state_path(root).exists()

    def run_onboarding_flow(self) -> CompanyOnboardingResult:
        """Run the guided setup and write Company quickstart artifacts."""

        self.output_func("👋 Welcome to Aider Plus Company Mode setup")
        warehouse_path = self._prompt_path(
            "Warehouse directory",
            self.defaults.get("warehouse_path") or default_warehouse_path(self.root),
        )
        WarehouseManager(warehouse_path).init()
        self.output_func(f"✓ Warehouse initialized at {warehouse_path}")

        template = self._prompt_template(
            str(self.defaults.get("template") or DEFAULT_TEMPLATE_KEY)
        )
        self.output_func(f"✓ Default template: {template}")

        github_repo = self._prompt(
            "GitHub repo for daemon issues (owner/repo, blank to skip)",
            str(self.defaults.get("github_repo") or ""),
        ).strip()
        github_token = self._prompt_secret(
            "GitHub token for daemon (blank to use GITHUB_TOKEN later)",
            str(self.defaults.get("github_token") or ""),
        ).strip()
        github_token_configured = bool(github_token or os.environ.get("GITHUB_TOKEN"))

        model_preferences = self._prompt_department_models()
        mcp_enabled = self._prompt_bool(
            "Enable MCP integrations for Company agents?",
            bool(self.defaults.get("mcp_enabled", False)),
        )

        company_dir = self.root / ".aider" / "company"
        company_dir.mkdir(parents=True, exist_ok=True)
        daemon_workflow = company_dir / "workflow.yml"
        self._write_daemon_workflow(daemon_workflow, warehouse_path, github_repo)

        config_path = self.root / ONBOARDING_STATE
        state = {
            "version": 1,
            "warehouse_path": str(warehouse_path),
            "default_template": template,
            "github_repo": github_repo,
            "github_token_configured": github_token_configured,
            "mcp_enabled": mcp_enabled,
            "model_preferences": model_preferences,
            "daemon_workflow_path": str(daemon_workflow),
        }
        config_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        self._write_env_hint(github_token)

        workflow_guide = self.root / WORKFLOW_GUIDE
        self.generate_workflow_guide(
            workflow_guide,
            warehouse_path=warehouse_path,
            template=template,
            github_repo=github_repo,
            daemon_workflow=daemon_workflow,
            mcp_enabled=mcp_enabled,
            model_preferences=model_preferences,
        )
        self.output_func(f"✓ Wrote {workflow_guide}")
        self.output_func("✅ Company Mode onboarding complete")
        return CompanyOnboardingResult(
            warehouse_path=str(warehouse_path),
            template=template,
            github_repo=github_repo,
            github_token_configured=github_token_configured,
            mcp_enabled=mcp_enabled,
            config_path=str(config_path),
            workflow_guide_path=str(workflow_guide),
            daemon_workflow_path=str(daemon_workflow),
            model_preferences=model_preferences,
        )

    def generate_workflow_guide(
        self,
        path: str | Path,
        *,
        warehouse_path: str | Path,
        template: str,
        github_repo: str,
        daemon_workflow: str | Path,
        mcp_enabled: bool,
        model_preferences: Mapping[str, Mapping[str, object]],
    ) -> Path:
        """Write a quickstart guide for the configured Company workflow."""

        template_obj = get_template(template)
        model_lines = [
            f"- {dept}: {prefs.get('model') or 'default model'}"
            f" ({'cache on' if prefs.get('cache') else 'cache off'})"
            for dept, prefs in model_preferences.items()
        ]
        repo_flag = f" --repo {github_repo}" if github_repo else ""
        content = f"""# Aider Plus Company Workflow

Welcome to Company Mode. This repo has been prepared for a guided Product → UX → Engineering → Review → QA → DevOps workflow.

## Quickstart

1. Review the warehouse: `{warehouse_path}`.
2. Create your first product:
   ```bash
   aider company new "Build my MVP" --template {template_obj.key} --warehouse {warehouse_path}
   ```
3. Run the issue daemon when you are ready to pick up GitHub or local tracker work:
   ```bash
   aider company daemon --workflow {daemon_workflow}{repo_flag} --once
   ```
4. Use approvals to keep humans in control of requirements, design, implementation, QA, and release gates.

## Selected Template

- `{template_obj.key}` — {template_obj.label}: {template_obj.description}

## Department Models

{chr(10).join(model_lines) if model_lines else '- Use the default Aider model for every department.'}

## GitHub Issues Daemon

- Repo: `{github_repo or 'not configured yet'}`
- Token: {'configured via onboarding/environment' if github_repo else 'set GITHUB_TOKEN before enabling GitHub tracking'}
- Workflow file: `{daemon_workflow}`

## MCP

MCP integrations are {'enabled' if mcp_enabled else 'disabled'}. Enable MCP later if Company agents need external tools or context servers.

## Core Loop

1. Product clarifies the MVP and writes acceptance criteria.
2. UX turns requirements into user flows, states, and accessibility notes.
3. Engineering implements in small repo-native changes.
4. Review and QA validate correctness, tests, and risk.
5. DevOps/Delivery prepare release notes, deployment proof, and follow-up issues.
"""
        path = Path(path)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_daemon_workflow(
        self, path: Path, warehouse_path: Path, github_repo: str
    ) -> None:
        tracker_kind = "github" if github_repo else "local"
        tracker_extra = (
            f"\n  repo: {github_repo}"
            if github_repo
            else "\n  path: .aider/company/issues.json"
        )
        path.write_text(
            f"""version: 1
name: company-onboarding
workspace_root: {warehouse_path}
tracker:
  kind: {tracker_kind}{tracker_extra}
runner:
  max_concurrent_agents: 1
""",
            encoding="utf-8",
        )

    def _write_env_hint(self, github_token: str) -> None:
        if not github_token:
            return
        env_path = self.root / ".env"
        line = f"GITHUB_TOKEN={github_token}\n"
        existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        if "GITHUB_TOKEN=" not in existing:
            env_path.write_text(
                existing
                + ("\n" if existing and not existing.endswith("\n") else "")
                + line,
                encoding="utf-8",
            )

    def _prompt_department_models(self) -> dict[str, dict[str, object]]:
        default_model = str(self.defaults.get("model") or "")
        configured = self.defaults.get("model_preferences")
        if isinstance(configured, dict):
            return {
                str(k): dict(v) for k, v in configured.items() if isinstance(v, dict)
            }
        result: dict[str, dict[str, object]] = {}
        for dept in DEPARTMENTS:
            model = self._prompt(f"Model for {dept} department", default_model).strip()
            cache = self._prompt_bool(f"Enable prompt caching for {dept}?", True)
            result[dept] = {"model": model, "cache": cache}
        return result

    def _prompt_template(self, default: str) -> str:
        keys = [template.key for template in list_templates()]
        answer = (
            self._prompt(
                "Default template (run `aider company templates` to inspect all)",
                default,
            ).strip()
            or default
        )
        try:
            return get_template(answer).key
        except ValueError:
            self.output_func(f"Unknown template `{answer}`; using `{default}`.")
            if default not in keys:
                default = DEFAULT_TEMPLATE_KEY
            return get_template(default).key

    def _prompt_path(self, label: str, default: object) -> Path:
        answer = self._prompt(label, str(default)).strip() or str(default)
        return Path(answer).expanduser().resolve()

    def _prompt_bool(self, label: str, default: bool) -> bool:
        suffix = "Y/n" if default else "y/N"
        answer = self.input_func(f"{label} [{suffix}]: ").strip().lower()
        if not answer:
            return default
        return answer in {"y", "yes", "true", "1", "on"}

    def _prompt_secret(self, label: str, default: str = "") -> str:
        return self._prompt(label, default)

    def _prompt(self, label: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        answer = self.input_func(f"{label}{suffix}: ")
        return answer if answer else default
