"""Shared GUI settings model, validation, preview, and persistence helpers.

Both the Streamlit browser UI and the zero-dependency Tkinter desktop UI use
this module so settings screens behave consistently even though the widgets are
implemented with different GUI toolkits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aider.settings import (
    COMPANY_AGENT_NAMES,
    PRIMARY_PROVIDER_API_KEYS,
    PROVIDER_API_KEYS,
    agent_api_key_env_name,
    agent_caching_env_name,
    agent_local_env_name,
    agent_model_env_name,
    apply_env_updates,
    collect_agent_env_updates,
    collect_provider_key_updates,
    parse_conf_text,
    read_env_values,
    upsert_conf_text,
    write_conf_text,
    write_env_updates,
)

SETTINGS_SECTIONS = (
    "Global Aider",
    "Per-Agent Overrides",
    "Provider Keys",
    "Advanced (.env + .aider.conf)",
)

MASK = "••••••••"


@dataclass
class SettingsAgentForm:
    """Per-agent settings captured by GUI forms."""

    model: str = ""
    caching: str | bool = "default"
    api_key: str = ""
    local: str = ""


@dataclass
class SettingsForm:
    """Toolkit-agnostic settings form values."""

    model: str = ""
    weak_model: str = ""
    editor_model: str = ""
    apply_now: bool = True
    provider_keys: dict[str, str] = field(default_factory=dict)
    extra_env: str = ""
    agents: dict[str, SettingsAgentForm] = field(default_factory=dict)
    conf_text: str = ""


@dataclass
class SettingsPreview:
    """Result of validating and previewing pending settings changes."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    env_updates: dict[str, str] = field(default_factory=dict)
    model_updates: dict[str, str] = field(default_factory=dict)
    conf_preview: str = ""
    restart_required: bool = True

    def render(self, include_secrets: bool = False) -> str:
        lines: list[str] = []
        if self.errors:
            lines.append("Validation errors:")
            lines.extend(f"- {error}" for error in self.errors)
            lines.append("")
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in self.warnings)
            lines.append("")
        if self.model_updates:
            lines.append(".aider.conf.yml model updates:")
            for key, value in self.model_updates.items():
                if value:
                    lines.append(f"- {key}: {value}")
            lines.append("")
        if self.env_updates:
            lines.append(".env updates:")
            for key, value in sorted(self.env_updates.items()):
                display = value if include_secrets or not _looks_secret(key) else MASK
                lines.append(f"- {key}={display}")
            lines.append("")
        if self.conf_preview:
            lines.append("Merged .aider.conf.yml preview:")
            lines.append(self.conf_preview.rstrip())
        if not lines:
            lines.append("No settings changes were detected.")
        if self.restart_required:
            lines.append("")
            lines.append("Company sessions will be restarted after apply.")
        return "\n".join(lines).strip()


def _looks_secret(key: str) -> bool:
    upper = key.upper()
    return (
        "KEY" in upper or "TOKEN" in upper or "SECRET" in upper or "PASSWORD" in upper
    )


def load_settings_form(
    env_path: Path, conf_path: Path, current_model: Any | None = None
) -> SettingsForm:
    """Load repo settings into a shared form object."""

    env_values = read_env_values(env_path)
    conf_text = conf_path.read_text(encoding="utf-8") if conf_path.exists() else ""
    conf_values = parse_conf_text(conf_text)
    defaults = {
        "model": getattr(current_model, "name", ""),
        "weak-model": getattr(getattr(current_model, "weak_model", None), "name", ""),
        "editor-model": getattr(
            getattr(current_model, "editor_model", None), "name", ""
        ),
    }
    agents: dict[str, SettingsAgentForm] = {}
    for agent_name in COMPANY_AGENT_NAMES:
        agents[agent_name] = SettingsAgentForm(
            model=env_values.get(agent_model_env_name(agent_name), ""),
            caching=env_values.get(agent_caching_env_name(agent_name), "default"),
            api_key=env_values.get(agent_api_key_env_name(agent_name), ""),
            local=env_values.get(agent_local_env_name(agent_name), ""),
        )
    return SettingsForm(
        model=conf_values.get("model") or defaults["model"],
        weak_model=conf_values.get("weak-model") or defaults["weak-model"],
        editor_model=conf_values.get("editor-model") or defaults["editor-model"],
        provider_keys={key: env_values.get(key, "") for key in PROVIDER_API_KEYS},
        agents=agents,
        conf_text=conf_text,
    )


def build_settings_preview(form: SettingsForm) -> SettingsPreview:
    """Validate GUI settings and return a save preview."""

    errors: list[str] = []
    warnings: list[str] = []
    model_updates = {
        "model": form.model.strip(),
        "weak-model": form.weak_model.strip(),
        "editor-model": form.editor_model.strip(),
    }
    if not model_updates["model"]:
        errors.append("Global Aider main model is required.")
    provider_updates = collect_provider_key_updates(
        form.provider_keys.get("OPENAI_API_KEY", ""),
        form.provider_keys.get("ANTHROPIC_API_KEY", ""),
        form.provider_keys.get("OPENROUTER_API_KEY", ""),
        form.extra_env,
        discord_bot_token=form.provider_keys.get("DISCORD_BOT_TOKEN", ""),
    )
    for line_number, line in enumerate(form.extra_env.splitlines(), start=1):
        stripped = line.strip()
        if stripped and "=" not in stripped:
            errors.append(
                f"Advanced .env line {line_number} must use KEY=value syntax."
            )
    agent_models: dict[str, str] = {}
    agent_caching: dict[str, str | bool] = {}
    agent_api_keys: dict[str, str] = {}
    agent_local_settings: dict[str, str] = {}
    for agent_name in COMPANY_AGENT_NAMES:
        values = form.agents.get(agent_name, SettingsAgentForm())
        cache_value = values.caching
        if isinstance(cache_value, str):
            normalized = cache_value.strip().lower() or "default"
            if normalized not in {
                "default",
                "true",
                "false",
                "1",
                "0",
                "yes",
                "no",
                "on",
                "off",
                "enabled",
                "disabled",
            }:
                errors.append(f"{agent_name} caching must be default, true, or false.")
            cache_value = normalized
        agent_models[agent_name] = values.model
        agent_caching[agent_name] = cache_value
        agent_api_keys[agent_name] = values.api_key
        agent_local_settings[agent_name] = values.local
    env_updates = provider_updates | collect_agent_env_updates(
        agent_models, agent_caching, agent_api_keys, agent_local_settings
    )
    if not any(key in provider_updates for key in PRIMARY_PROVIDER_API_KEYS):
        warnings.append(
            "No primary provider API key is set here; direct Aider may rely on your shell environment."
        )
    try:
        conf_preview = upsert_conf_text(form.conf_text, model_updates)
    except (
        Exception
    ) as err:  # defensive: upsert is simple but keep UI validation friendly
        conf_preview = form.conf_text
        errors.append(f"Could not build .aider.conf.yml preview: {err}")
    return SettingsPreview(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        env_updates=env_updates,
        model_updates=model_updates,
        conf_preview=conf_preview,
    )


def save_settings(
    env_path: Path, conf_path: Path, form: SettingsForm
) -> SettingsPreview:
    """Validate, persist, and apply settings to the current process environment."""

    preview = build_settings_preview(form)
    if not preview.valid:
        return preview
    write_env_updates(env_path, preview.env_updates)
    apply_env_updates(preview.env_updates)
    write_conf_text(conf_path, form.conf_text, preview.model_updates)
    return preview
