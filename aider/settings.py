"""Shared settings helpers for browser and desktop launchers."""

from __future__ import annotations

import os
import re
from pathlib import Path

COMPANY_AGENT_NAMES = (
    "coo",
    "product",
    "ux",
    "engineering",
    "reviewer",
    "qa",
    "devops",
)
PROVIDER_API_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)


def agent_model_env_name(agent_name: str) -> str:
    return f"AIDER_COMPANY_MODEL_{agent_name.upper()}"


def agent_caching_env_name(agent_name: str) -> str:
    return f"AIDER_COMPANY_CACHING_{agent_name.upper()}"


def agent_api_key_env_name(agent_name: str) -> str:
    return f"AIDER_COMPANY_API_KEY_{agent_name.upper()}"


def agent_local_env_name(agent_name: str) -> str:
    return f"AIDER_COMPANY_LOCAL_{agent_name.upper()}"


def read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_updates(path: Path, updates: dict[str, str]) -> None:
    if not updates:
        return
    existing = read_env_values(path) if path.exists() else {}
    existing.update({key: value for key, value in updates.items() if value is not None})
    lines = [f"{key}={value}" for key, value in sorted(existing.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_env_updates(updates: dict[str, str]) -> None:
    for key, value in updates.items():
        os.environ[key] = value


def collect_provider_key_updates(
    openai_key: str,
    anthropic_key: str,
    openrouter_key: str,
    provider_keys: str,
) -> dict[str, str]:
    updates: dict[str, str] = {}
    for key, value in (
        ("OPENAI_API_KEY", openai_key),
        ("ANTHROPIC_API_KEY", anthropic_key),
        ("OPENROUTER_API_KEY", openrouter_key),
    ):
        if value:
            updates[key] = value.strip()
    for line in provider_keys.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and value:
            updates[key.strip()] = value.strip()
    return updates


def collect_agent_env_updates(
    agent_models: dict[str, str],
    agent_caching: dict[str, bool | str],
    agent_api_keys: dict[str, str] | None = None,
    agent_local_settings: dict[str, str] | None = None,
) -> dict[str, str]:
    updates: dict[str, str] = {}
    for agent_name in COMPANY_AGENT_NAMES:
        model = (agent_models.get(agent_name) or "").strip()
        if model:
            updates[agent_model_env_name(agent_name)] = model
        if agent_name in agent_caching:
            value = agent_caching[agent_name]
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"", "default"}:
                    continue
                enabled = normalized in {"1", "true", "yes", "on", "enabled", "enable"}
            else:
                enabled = bool(value)
            updates[agent_caching_env_name(agent_name)] = "true" if enabled else "false"
        api_key = ((agent_api_keys or {}).get(agent_name) or "").strip()
        if api_key:
            updates[agent_api_key_env_name(agent_name)] = api_key
        local_setting = ((agent_local_settings or {}).get(agent_name) or "").strip()
        if local_setting:
            updates[agent_local_env_name(agent_name)] = local_setting
    return updates


def read_conf_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    return parse_conf_text(path.read_text(encoding="utf-8"))


def parse_conf_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([a-zA-Z0-9_-]+)\s*:\s*(.*?)\s*$", line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return values


def upsert_conf_text(text: str, updates: dict[str, str]) -> str:
    lines = text.splitlines()
    applied: set[str] = set()
    result: list[str] = []
    for line in lines:
        match = re.match(r"^(\s*)([a-zA-Z0-9_-]+)(\s*:\s*)(.*?)(\s*)$", line)
        if not match:
            result.append(line)
            continue
        key = match.group(2)
        value = updates.get(key)
        if value:
            result.append(
                f"{match.group(1)}{key}{match.group(3)}{value}{match.group(5)}"
            )
            applied.add(key)
        else:
            result.append(line)
    for key, value in sorted(updates.items()):
        if value and key not in applied:
            result.append(f"{key}: {value}")
    return "\n".join(result).rstrip() + "\n"


def write_conf_text(
    path: Path, text: str, updates: dict[str, str] | None = None
) -> None:
    final_text = upsert_conf_text(text, updates or {})
    path.write_text(final_text, encoding="utf-8")


def write_conf_updates(path: Path, updates: dict[str, str]) -> None:
    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    write_conf_text(path, existing_text, updates)
