from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import inspect
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aider.company.schemas import CompanyTask
from aider.mcp.config import MCPConfig, MCPServerConfig, MCPToolPolicy

MCPApprovalHandler = Callable[[dict[str, Any]], Awaitable[bool] | bool]


class MCPDependencyError(RuntimeError):
    """Raised when MCP functionality is requested without the optional SDK."""


@dataclass
class MCPToolRef:
    server_name: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    policy: MCPToolPolicy = field(default_factory=MCPToolPolicy)

    @property
    def aider_name(self) -> str:
        return f"mcp__{self.server_name}__{self.name}"


@dataclass
class MCPConnector:
    server: MCPServerConfig
    client: Any
    session: Any
    tools: list[MCPToolRef]
    context_stack: contextlib.AsyncExitStack | None = None


class MCPClientManager:
    """Lazy MCP client manager for external tool/context servers.

    One manager can maintain multiple project/task scoped connector pools. The
    manager never routes model calls; it only discovers and invokes tools that
    can then be exposed through Aider Plus's ToolRegistry.
    """

    def __init__(
        self,
        config: MCPConfig | None = None,
        *,
        approval_handler: MCPApprovalHandler | None = None,
        approval_manager: Any | None = None,
    ):
        self.config = config or MCPConfig()
        self.approval_handler = approval_handler
        self.approval_manager = approval_manager
        self._approval_aware_tool_policies: dict[str, MCPToolPolicy] = {}
        self._connectors_by_scope: dict[str, list[MCPConnector]] = {}

    def register_approval_aware_tool(
        self, tool_name: str, permission_level: str
    ) -> MCPToolPolicy:
        """Register local policy metadata for an MCP tool.

        ``read_only`` tools may execute without a human gate;
        ``requires_approval`` tools must pass through ApprovalManager or the
        configured approval handler before execution.
        """

        if permission_level not in {"read_only", "requires_approval"}:
            raise ValueError("permission_level must be read_only or requires_approval")
        policy = MCPToolPolicy(
            read_only=permission_level == "read_only",
            requires_approval=permission_level == "requires_approval",
        )
        self._approval_aware_tool_policies[tool_name] = policy
        for server in self.config.servers.values():
            server.tool_policies[tool_name] = policy
        return policy

    async def ensure_connected(
        self,
        *,
        project_dir: str | None = None,
        task_dir: str | None = None,
        scope_key: str | None = None,
    ) -> list[MCPConnector]:
        if not self.config.enabled:
            return []
        scope = scope_key or f"{project_dir or ''}:{task_dir or ''}"
        if scope in self._connectors_by_scope:
            return self._connectors_by_scope[scope]

        connectors: list[MCPConnector] = []
        for server in self.config.servers.values():
            if not server.enabled:
                continue
            resolved = server.interpolate(project_dir=project_dir, task_dir=task_dir)
            connectors.append(await self._connect_server(resolved))
        self._connectors_by_scope[scope] = connectors
        return connectors

    async def list_tools(self, **kwargs: Any) -> list[MCPToolRef]:
        connectors = await self.ensure_connected(**kwargs)
        return [tool for connector in connectors for tool in connector.tools]

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        connector = self._find_connector(server_name)
        tool = self._find_tool(connector, tool_name)
        await self._ensure_approved(connector.server, tool, arguments)
        result = await connector.session.call_tool(tool_name, arguments)
        return self._serialize_result(result)

    async def close(self) -> None:
        for connectors in self._connectors_by_scope.values():
            for connector in connectors:
                if connector.context_stack is not None:
                    await connector.context_stack.aclose()
        self._connectors_by_scope.clear()

    async def _connect_server(self, server: MCPServerConfig) -> MCPConnector:
        if importlib.util.find_spec("mcp") is None:  # pragma: no cover - optional dep
            raise MCPDependencyError(
                "Install the optional MCP dependencies to use MCP client support."
            )
        from mcp import ClientSession

        stack = contextlib.AsyncExitStack()
        if server.transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            if not server.command:
                raise ValueError(
                    f"MCP server '{server.name}' requires a command for stdio"
                )
            env = os.environ.copy()
            env.update(server.env)
            read, write = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=server.command, args=server.args, env=env
                    )
                )
            )
        elif server.transport == "streamable_http":
            from mcp.client.streamable_http import streamablehttp_client

            if not server.url:
                raise ValueError(f"MCP server '{server.name}' requires a url")
            read, write, *_ = await stack.enter_async_context(
                streamablehttp_client(server.url)
            )
        elif server.transport == "sse":
            from mcp.client.sse import sse_client

            if not server.url:
                raise ValueError(f"MCP server '{server.name}' requires a url")
            read, write = await stack.enter_async_context(sse_client(server.url))
        else:
            raise ValueError(f"Unsupported MCP transport: {server.transport}")

        session = await stack.enter_async_context(ClientSession(read, write))
        await asyncio.wait_for(session.initialize(), timeout=server.timeout_seconds)
        tools_result = await asyncio.wait_for(
            session.list_tools(), timeout=server.timeout_seconds
        )
        tools = [
            self._normalize_tool(server, raw_tool)
            for raw_tool in getattr(tools_result, "tools", [])
        ]
        return MCPConnector(
            server=server,
            client=session,
            session=session,
            tools=tools,
            context_stack=stack,
        )

    def _normalize_tool(self, server: MCPServerConfig, raw_tool: Any) -> MCPToolRef:
        raw_get = (
            raw_tool.get
            if isinstance(raw_tool, dict)
            else lambda _key, default=None: default
        )
        name = str(getattr(raw_tool, "name", "") or raw_get("name", ""))
        description = str(
            getattr(raw_tool, "description", "") or raw_get("description", "")
        )
        schema = getattr(raw_tool, "inputSchema", None)
        if schema is None:
            schema = getattr(raw_tool, "input_schema", None)
        if schema is None and isinstance(raw_tool, dict):
            schema = raw_tool.get("inputSchema") or raw_tool.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        policy = server.tool_policies.get(
            name, self._approval_aware_tool_policies.get(name, MCPToolPolicy())
        )
        return MCPToolRef(
            server_name=server.name,
            name=name,
            description=description,
            input_schema=schema,
            policy=policy,
        )

    def _find_connector(self, server_name: str) -> MCPConnector:
        for connectors in self._connectors_by_scope.values():
            for connector in connectors:
                if connector.server.name == server_name:
                    return connector
        raise ValueError(f"MCP server is not connected: {server_name}")

    @staticmethod
    def _find_tool(connector: MCPConnector, tool_name: str) -> MCPToolRef:
        for tool in connector.tools:
            if tool.name == tool_name:
                return tool
        raise ValueError(f"Unknown MCP tool: {connector.server.name}.{tool_name}")

    async def _ensure_approved(
        self,
        server: MCPServerConfig,
        tool: MCPToolRef,
        arguments: dict[str, Any],
    ) -> None:
        if not tool.policy.enabled:
            raise PermissionError(f"MCP tool is disabled: {tool.aider_name}")
        if server.allowed_tools and tool.name not in server.allowed_tools:
            raise PermissionError(
                f"MCP tool is not allowed by server policy: {tool.aider_name}"
            )
        if not tool.policy.requires_approval:
            return
        request = {
            "server": server.name,
            "tool": tool.name,
            "aider_tool": tool.aider_name,
            "arguments": arguments,
            "read_only": tool.policy.read_only,
            "approval_reason": self._approval_reason(server, tool),
        }
        if self.approval_manager is not None:
            task = CompanyTask(
                task_id=f"mcp-approval-{server.name}-{tool.name}",
                origin="mcp",
                target="engineering",
                artifact_type="mcp_tool_call",
                payload=request,
                blocking=True,
                context={
                    "gate_name": "mcp_tool_approval",
                    "approver_role": "ceo",
                    "artifact_preview": str(request)[:1500],
                    "handoff_to": "engineering",
                    "approval_reason": request["approval_reason"],
                },
            )
            decision = await self.approval_manager.create_request(task)
            if hasattr(self.approval_manager, "close_request"):
                self.approval_manager.close_request(task.task_id)
            if not bool(getattr(decision, "approved", decision)):
                raise PermissionError(
                    self._denied_message(tool, getattr(decision, "reason", None))
                )
            return
        if self.approval_handler is None:
            raise PermissionError(
                f"MCP tool requires approval but no approval handler is configured: {tool.aider_name}"
            )
        approved = self.approval_handler(request)
        if inspect.isawaitable(approved):
            approved = await approved
        approved_bool, reason = self._approval_result(approved)
        if not approved_bool:
            raise PermissionError(self._denied_message(tool, reason))

    @staticmethod
    def _approval_result(result: Any) -> tuple[bool, str | None]:
        if isinstance(result, dict):
            return bool(result.get("approved")), result.get("reason")
        return bool(getattr(result, "approved", result)), getattr(
            result, "reason", None
        )

    @staticmethod
    def _approval_reason(server: MCPServerConfig, tool: MCPToolRef) -> str:
        if tool.policy.read_only and not tool.policy.requires_approval:
            return "Read-only inspection; no shared approval gate is required."
        return (
            f"{tool.aider_name} on MCP server {server.name} is marked "
            "requires_approval in MCP policy; it can mutate state, trigger work, "
            "approve changes, or affect external systems."
        )

    @staticmethod
    def _denied_message(tool: MCPToolRef, reason: str | None = None) -> str:
        detail = reason or "No denial reason provided."
        return f"Denied MCP operation {tool.aider_name}: {detail}"

    @staticmethod
    def _serialize_result(result: Any) -> Any:
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if hasattr(result, "dict"):
            return result.dict()
        if isinstance(result, (str, int, float, bool, list, dict)) or result is None:
            return result
        content = getattr(result, "content", None)
        if content is not None:
            return {"content": content}
        return str(result)
