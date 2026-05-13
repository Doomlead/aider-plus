from __future__ import annotations

from typing import Any

from aider.agent.tools import Tool
from aider.mcp.manager import MCPClientManager, MCPToolRef


def mcp_tool_to_aider_tool(manager: MCPClientManager, tool_ref: MCPToolRef) -> Tool:
    """Convert an MCP tool descriptor into an Aider Plus ToolRegistry tool."""

    async def call_mcp_tool(**arguments: Any) -> Any:
        return await manager.call_tool(tool_ref.server_name, tool_ref.name, arguments)

    return Tool(
        name=tool_ref.aider_name,
        description=f"[MCP:{tool_ref.server_name}] {tool_ref.description or tool_ref.name}",
        func=call_mcp_tool,
        parameters={
            "type": "function",
            "function": {
                "name": tool_ref.aider_name,
                "description": f"[MCP:{tool_ref.server_name}] {tool_ref.description or tool_ref.name}",
                "parameters": tool_ref.input_schema
                or {"type": "object", "properties": {}},
            },
        },
    )
