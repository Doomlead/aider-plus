"""Guided first-run setup for Aider Plus Company Mode."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from aider.company.templates import DEFAULT_TEMPLATE_KEY, get_template, list_templates
from aider.company.warehouse import WarehouseManager, default_warehouse_path
from aider.mcp.server import list_builtin_mcp_tools

DEPARTMENTS = ("product", "ux", "engineering", "reviewer", "qa", "devops")
ONBOARDING_STATE = Path(".aider") / "company" / "onboarding.json"
WORKFLOW_GUIDE = "AIDER_WORKFLOW.md"
ENV_EXAMPLE = ".env.example"
MINIMAL_ONBOARDING_STEPS = 5
ADVANCED_ONBOARDING_STEPS = 7


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def detect_onboarding_defaults() -> dict[str, object]:
    """Infer first-run defaults from common environment variables."""

    detected: dict[str, object] = {}
    for env_name, default_name in (
        ("AIDER_COMPANY_WAREHOUSE", "warehouse_path"),
        ("AIDER_COMPANY_TEMPLATE", "template"),
        ("GITHUB_REPO", "github_repo"),
        ("GITHUB_TOKEN", "github_token"),
    ):
        value = os.environ.get(env_name, "").strip()
        if value:
            detected[default_name] = value

    model = os.environ.get("AIDER_MODEL", "").strip()
    if not model:
        provider_model_defaults = (
            ("OPENAI_API_KEY", "gpt-5.5"),
            ("ANTHROPIC_API_KEY", "anthropic/claude-sonnet-4-5"),
            ("OPENROUTER_API_KEY", "openrouter/deepseek/deepseek-r1:free"),
            ("GEMINI_API_KEY", "gemini/gemini-2.5-pro"),
            ("DEEPSEEK_API_KEY", "deepseek/deepseek-chat"),
        )
        for env_name, env_model in provider_model_defaults:
            if os.environ.get(env_name, "").strip():
                model = env_model
                break
    if model:
        detected["model"] = model

    if os.environ.get("AIDER_MCP_CONFIG", "").strip() or _env_flag(
        "AIDER_COMPANY_ENABLE_MCP"
    ):
        detected["mcp_enabled"] = True

    return detected


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
    env_example_path: str = ""
    first_product_path: str = ""
    api_key_validation: dict[str, bool] = field(default_factory=dict)
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
        self.defaults = {**detect_onboarding_defaults(), **dict(defaults or {})}

    @staticmethod
    def state_path(root: str | Path | None = None) -> Path:
        return Path(root or Path.cwd()).expanduser().resolve() / ONBOARDING_STATE

    @staticmethod
    def has_completed(root: str | Path | None = None) -> bool:
        return CompanyOnboarding.state_path(root).exists()

    def run_onboarding_flow(self) -> CompanyOnboardingResult:
        """Run the guided setup and write Company quickstart artifacts."""

        advanced = bool(self.defaults.get("advanced", False))
        total_steps = (
            ADVANCED_ONBOARDING_STEPS if advanced else MINIMAL_ONBOARDING_STEPS
        )
        step_index = 0

        def step(label: str) -> None:
            nonlocal step_index
            step_index += 1
            self.output_func(f"Step {step_index} of {total_steps}: {label}")

        self.output_func("👋 Welcome to Aider Plus Company Mode setup")
        if advanced:
            self.output_func(
                "Advanced setup is enabled; all optional choices will be shown."
            )
        else:
            self.output_func(
                "Minimal setup is enabled; daemon, MCP, and per-department overrides can be configured later with `aider company init --advanced`."
            )

        step("Choose a warehouse")
        warehouse_path = self._prompt_path(
            "Warehouse directory",
            self.defaults.get("warehouse_path") or default_warehouse_path(self.root),
        )
        WarehouseManager(warehouse_path).init()
        self.output_func(f"✓ Warehouse initialized at {warehouse_path}")

        step("Choose a starter template")
        template = self._prompt_template(
            str(self.defaults.get("template") or DEFAULT_TEMPLATE_KEY)
        )
        self.output_func(f"✓ Default template: {template}")

        if advanced:
            step("Configure GitHub issue tracking")
            github_repo = self._prompt(
                "GitHub repo for daemon issues (owner/repo, blank to skip)",
                str(self.defaults.get("github_repo") or ""),
            ).strip()
            github_token = self._prompt_secret(
                "GitHub token for daemon (blank to use GITHUB_TOKEN later)",
                str(self.defaults.get("github_token") or ""),
            ).strip()
        else:
            github_repo = str(self.defaults.get("github_repo") or "").strip()
            github_token = str(self.defaults.get("github_token") or "").strip()
            if github_repo:
                self.output_func(
                    f"✓ Detected GitHub repo from defaults/environment: {github_repo}"
                )
            else:
                self.output_func(
                    "Configure later: add GITHUB_REPO or re-run `aider company init --advanced` for GitHub issue tracking."
                )
        github_token_configured = bool(github_token or os.environ.get("GITHUB_TOKEN"))

        step("Choose model defaults")
        model_preferences = self._prompt_department_models(advanced=advanced)
        api_key_validation = self.validate_api_keys(model_preferences)
        self._report_api_key_validation(api_key_validation)

        if advanced:
            step("Configure MCP integrations")
            mcp_enabled = self._prompt_bool(
                "Enable MCP integrations for Company agents?",
                bool(self.defaults.get("mcp_enabled", False)),
            )
        else:
            mcp_enabled = bool(self.defaults.get("mcp_enabled", False))
            self.output_func(
                "✓ MCP integrations detected from environment."
                if mcp_enabled
                else "Configure later: re-run with `--advanced` to enable MCP integrations."
            )
        mcp_tools = self._prompt_mcp_tools(mcp_enabled)

        step("Write quickstart files")
        company_dir = self.root / ".aider" / "company"
        company_dir.mkdir(parents=True, exist_ok=True)
        daemon_workflow = company_dir / "workflow.yml"
        self._write_daemon_workflow(daemon_workflow, warehouse_path, github_repo)

        config_path = self.root / ONBOARDING_STATE
        state = {
            "version": 1,
            "onboarding_mode": "advanced" if advanced else "minimal",
            "warehouse_path": str(warehouse_path),
            "default_template": template,
            "default_template_mode": str(
                self.defaults.get("default_template_mode") or "auto"
            ),
            "github_repo": github_repo,
            "github_token_configured": github_token_configured,
            "mcp_enabled": mcp_enabled,
            "mcp_tools": mcp_tools,
            "api_key_validation": api_key_validation,
            "model_preferences": model_preferences,
            "daemon_workflow_path": str(daemon_workflow),
        }
        config_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        self._write_env_hint(github_token)

        env_example = self.root / ENV_EXAMPLE
        self.generate_env_example(
            env_example,
            model_preferences=model_preferences,
            github_repo=github_repo,
            mcp_enabled=mcp_enabled,
        )

        workflow_guide = self.root / WORKFLOW_GUIDE
        self.generate_workflow_guide(
            workflow_guide,
            warehouse_path=warehouse_path,
            template=template,
            github_repo=github_repo,
            daemon_workflow=daemon_workflow,
            mcp_enabled=mcp_enabled,
            model_preferences=model_preferences,
            env_example=env_example,
        )
        self.output_func(f"✓ Wrote {workflow_guide}")
        self.output_func(f"✓ Wrote {env_example}")

        step("Create a first product or configure later")
        first_product_path = self._maybe_create_first_product(warehouse_path, template)
        if first_product_path:
            state["first_product_path"] = first_product_path
            config_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

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
            env_example_path=str(env_example),
            first_product_path=first_product_path,
            api_key_validation=api_key_validation,
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
        env_example: str | Path,
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
2. Copy `{env_example}` to `.env` and fill any missing provider keys.
3. Create your first product:
   ```bash
   aider company new "Build my MVP" --template {template_obj.key} --warehouse {warehouse_path}
   ```
4. Run the issue daemon when you are ready to pick up GitHub or local tracker work:
   ```bash
   aider company daemon --workflow {daemon_workflow}{repo_flag} --once
   ```
5. Use approvals to keep humans in control of requirements, design, implementation, QA, and release gates.

## Selected Template

- `{template_obj.key}` — {template_obj.label}: {template_obj.description}

## Department Models

{chr(10).join(model_lines) if model_lines else '- Use the default Aider model for every department.'}

## GitHub Issues Daemon

- Repo: `{github_repo or 'not configured yet'}`
- Token: {'configured via onboarding/environment' if github_repo else 'set GITHUB_TOKEN before enabling GitHub tracking'}
- Workflow file: `{daemon_workflow}`

## MCP

MCP integrations are {'enabled' if mcp_enabled else 'disabled'}. When enabled, built-in tools use approval defaults: read-only inspection tools execute directly; mutating tools require human approval before they run. Inspect tool policies with `aider mcp tools`.

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

    def generate_env_example(
        self,
        path: str | Path,
        *,
        model_preferences: Mapping[str, Mapping[str, object]],
        github_repo: str,
        mcp_enabled: bool,
    ) -> Path:
        """Write a provider-specific environment template for onboarding choices."""

        required_keys = self.required_api_keys(model_preferences)
        lines = [
            "# Aider Plus Company Mode environment",
            "# Copy this file to .env and fill in only the keys your selected models need.",
            "",
        ]
        for env_name in required_keys:
            lines.append(f"{env_name}=your_{env_name.lower()}_here")
        if github_repo:
            lines.extend(
                [
                    "",
                    "# Required for the GitHub Issues daemon.",
                    "GITHUB_TOKEN=ghp_your_token_here",
                ]
            )
            lines.append(f"GITHUB_REPO={github_repo}")
        if mcp_enabled:
            lines.extend(
                [
                    "",
                    "# Optional: point Company agents at your MCP configuration.",
                    "AIDER_MCP_CONFIG=.aider/mcp.json",
                ]
            )
        if len(lines) == 3:
            lines.append(
                "# No provider-specific keys were inferred from the selected models."
            )
        path = Path(path)
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return path

    def validate_api_keys(
        self, model_preferences: Mapping[str, Mapping[str, object]]
    ) -> dict[str, bool]:
        """Return whether each inferred provider key is currently configured."""

        return {
            env_name: bool(os.environ.get(env_name))
            for env_name in self.required_api_keys(model_preferences)
        }

    def required_api_keys(
        self, model_preferences: Mapping[str, Mapping[str, object]]
    ) -> tuple[str, ...]:
        env_names: set[str] = set()
        for prefs in model_preferences.values():
            model = str(prefs.get("model") or "").lower()
            if not model:
                continue
            if "openrouter/" in model:
                env_names.add("OPENROUTER_API_KEY")
            elif "anthropic/" in model or "claude" in model or "sonnet" in model:
                env_names.add("ANTHROPIC_API_KEY")
            elif "gemini" in model:
                env_names.add("GEMINI_API_KEY")
            elif "deepseek" in model:
                env_names.add("DEEPSEEK_API_KEY")
            elif model.startswith(("gpt-", "openai/", "o1", "o3", "o4")):
                env_names.add("OPENAI_API_KEY")
        return tuple(sorted(env_names))

    def _report_api_key_validation(self, validation: Mapping[str, bool]) -> None:
        if not validation:
            self.output_func("✓ No provider API keys inferred from selected models.")
            return
        missing = [env_name for env_name, present in validation.items() if not present]
        if missing:
            self.output_func(
                "⚠ Missing API keys for selected models: " + ", ".join(missing)
            )
            self.output_func("  Add them to .env using the generated .env.example.")
        else:
            self.output_func("✓ Required API keys are present for selected models.")

    def _maybe_create_first_product(self, warehouse_path: Path, template: str) -> str:
        create_now = bool(self.defaults.get("first_product_now", False))
        if "first_product_now" not in self.defaults:
            create_now = self._prompt_bool(
                "Would you like to create your first product now?", False
            )
        if not create_now:
            self.output_func(
                'Next step: run `aider company new "Build my MVP" --template '
                f"{template} --warehouse {warehouse_path}` when you are ready."
            )
            return ""

        idea = self._prompt(
            "First product idea",
            str(
                self.defaults.get("first_product_idea")
                or "Build my first MVP with Aider Plus"
            ),
        ).strip()
        name = self._prompt(
            "First product name",
            str(self.defaults.get("first_product_name") or "first-product"),
        ).strip()
        if not idea or not name:
            self.output_func(
                "Skipping first product creation because idea/name was blank."
            )
            return ""
        record = WarehouseManager(warehouse_path).create_product(
            name=name, idea=idea, template=template
        )
        self.output_func(f"✓ Created first product repo at {record.path}")
        return record.path

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

    def _prompt_mcp_tools(self, mcp_enabled: bool) -> list[dict[str, str]]:
        """Choose MCP built-in tools and preserve approval-first defaults."""

        if not mcp_enabled:
            return []
        configured = self.defaults.get("mcp_tools")
        tools = list_builtin_mcp_tools()
        if isinstance(configured, dict):
            tools = [tool for tool in tools if bool(configured.get(tool["name"], True))]
        self.output_func(
            "✓ MCP tool defaults: read-only tools enabled; mutating tools require approval. "
            "Run `aider mcp tools` to inspect or adjust policy."
        )
        return tools

    def _prompt_department_models(
        self, *, advanced: bool = True
    ) -> dict[str, dict[str, object]]:
        default_model = str(self.defaults.get("model") or "")
        configured = self.defaults.get("model_preferences")
        if isinstance(configured, dict):
            return {
                str(k): dict(v) for k, v in configured.items() if isinstance(v, dict)
            }
        if not advanced:
            model = self._prompt(
                "Default model for Company agents", default_model
            ).strip()
            return {dept: {"model": model, "cache": True} for dept in DEPARTMENTS}
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
