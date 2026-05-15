"""Public MCP exports with lazy loading to avoid agent/MCP import cycles."""

__all__ = [
    "AiderPlusMCPServer",
    "AiderPlusMCPServerConfig",
    "MCPClientManager",
    "MCPConfig",
    "MCPConnector",
    "MCPDependencyError",
    "MCPServerConfig",
    "MCPToolPolicy",
    "MCPToolRef",
    "mcp_tool_to_aider_tool",
]


def __getattr__(name):
    if name == "mcp_tool_to_aider_tool":
        from aider.mcp.adapters import mcp_tool_to_aider_tool

        return mcp_tool_to_aider_tool
    if name in {"MCPConfig", "MCPServerConfig", "MCPToolPolicy"}:
        from aider.mcp.config import MCPConfig, MCPServerConfig, MCPToolPolicy

        return {
            "MCPConfig": MCPConfig,
            "MCPServerConfig": MCPServerConfig,
            "MCPToolPolicy": MCPToolPolicy,
        }[name]
    if name in {"MCPClientManager", "MCPConnector", "MCPDependencyError", "MCPToolRef"}:
        from aider.mcp.manager import MCPClientManager, MCPConnector, MCPDependencyError, MCPToolRef

        return {
            "MCPClientManager": MCPClientManager,
            "MCPConnector": MCPConnector,
            "MCPDependencyError": MCPDependencyError,
            "MCPToolRef": MCPToolRef,
        }[name]
    if name in {"AiderPlusMCPServer", "AiderPlusMCPServerConfig"}:
        from aider.mcp.server import AiderPlusMCPServer, AiderPlusMCPServerConfig

        return {
            "AiderPlusMCPServer": AiderPlusMCPServer,
            "AiderPlusMCPServerConfig": AiderPlusMCPServerConfig,
        }[name]
    raise AttributeError(name)
