from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, cast

MCPTransport = Literal["stdio", "streamable_http", "sse"]
MCP_TRANSPORTS: frozenset[str] = frozenset(("stdio", "streamable_http", "sse"))


def _coerce_string_list(value: Any) -> list[str]:
    """Return a clean list of strings without treating one string as characters."""

    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if item is not None]


def _coerce_transport(value: Any) -> MCPTransport:
    transport = str(value or "stdio")
    if transport not in MCP_TRANSPORTS:
        raise ValueError(
            f"Unsupported MCP transport: {transport}. "
            f"Expected one of: {', '.join(sorted(MCP_TRANSPORTS))}."
        )
    return cast(MCPTransport, transport)


@dataclass
class MCPToolPolicy:
    """Per-tool execution policy for MCP tools."""

    enabled: bool = True
    read_only: bool = False
    requires_approval: bool = False
    allowed_departments: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPToolPolicy":
        return cls(
            enabled=bool(data.get("enabled", True)),
            read_only=bool(data.get("read_only", False)),
            requires_approval=bool(data.get("requires_approval", False)),
            allowed_departments=_coerce_string_list(data.get("allowed_departments")),
        )


@dataclass
class MCPServerConfig:
    """Configuration for one external MCP server.

    This is intentionally separate from model/provider configuration: MCP
    controls external tools and context, while LiteLLM/LiteLLM Proxy controls
    model access.
    """

    name: str
    transport: MCPTransport = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    allowed_departments: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    tool_policies: dict[str, MCPToolPolicy] = field(default_factory=dict)
    timeout_seconds: float = 30.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPServerConfig":
        policies = {
            str(name): MCPToolPolicy.from_dict(
                policy if isinstance(policy, dict) else {}
            )
            for name, policy in (data.get("tool_policies") or {}).items()
        }
        return cls(
            name=str(data.get("name", "")),
            transport=_coerce_transport(data.get("transport", "stdio")),
            command=data.get("command"),
            args=_coerce_string_list(data.get("args")),
            url=data.get("url"),
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
            enabled=bool(data.get("enabled", True)),
            allowed_departments=_coerce_string_list(data.get("allowed_departments")),
            allowed_tools=_coerce_string_list(data.get("allowed_tools")),
            tool_policies=policies,
            timeout_seconds=float(data.get("timeout_seconds", 30.0) or 30.0),
        )

    def interpolate(
        self, *, project_dir: str | None = None, task_dir: str | None = None
    ) -> "MCPServerConfig":
        replacements = {
            "${projectDir}": project_dir or "",
            "${taskDir}": task_dir or project_dir or "",
        }

        def replace(value: str | None) -> str | None:
            if value is None:
                return None
            for key, replacement in replacements.items():
                value = value.replace(key, replacement)
            return value

        return MCPServerConfig(
            name=self.name,
            transport=self.transport,
            command=replace(self.command),
            args=[replace(arg) or "" for arg in self.args],
            url=replace(self.url),
            env={key: replace(value) or "" for key, value in self.env.items()},
            enabled=self.enabled,
            allowed_departments=list(self.allowed_departments),
            allowed_tools=list(self.allowed_tools),
            tool_policies=dict(self.tool_policies),
            timeout_seconds=self.timeout_seconds,
        )


@dataclass
class MCPConfig:
    """Top-level MCP configuration."""

    enabled: bool = False
    servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    default_timeout_seconds: float = 30.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPConfig":
        servers: dict[str, MCPServerConfig] = {}
        for name, raw in (data.get("servers") or {}).items():
            if not isinstance(raw, dict):
                continue
            merged = {"name": name, **raw}
            server = MCPServerConfig.from_dict(merged)
            servers[server.name or str(name)] = server
        return cls(
            enabled=bool(data.get("enabled", False)),
            servers=servers,
            default_timeout_seconds=float(
                data.get("default_timeout_seconds", 30.0) or 30.0
            ),
        )

    @classmethod
    def from_env(cls) -> "MCPConfig":
        enabled = os.environ.get("AIDER_MCP_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "enabled",
        }
        return cls(enabled=enabled)
