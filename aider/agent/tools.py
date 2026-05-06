from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    parameters: Dict


class ToolPermissionError(PermissionError):
    """Raised when a department attempts to dispatch a non-whitelisted tool."""

    def __init__(self, *, department: str, tool_name: str, allowed_tools: list[str]):
        self.department = department
        self.tool_name = tool_name
        self.allowed_tools = allowed_tools
        super().__init__(
            f"Department '{department}' is not allowed to use tool '{tool_name}'."
        )

    def to_dict(self) -> dict:
        return {
            "type": "permission_violation",
            "department": self.department,
            "tool_name": self.tool_name,
            "allowed_tools": self.allowed_tools,
            "message": str(self),
        }


class ToolRegistry:
    def __init__(self, *, department: Optional[object] = None):
        self.tools: Dict[str, Tool] = {}
        self.department = department

    def set_department(self, department: object) -> None:
        self.department = department

    def register(self, tool: Tool):
        self.tools[tool.name] = tool

    def get_tool_definitions(self) -> list:
        """Return list of tool schemas for LLM tool calling."""
        return [t.parameters for t in self.tools.values()]

    def execute(self, name: str, arguments: dict) -> Any:
        self._authorize(name)
        if name in self.tools:
            return self.tools[name].func(**arguments)
        raise ValueError(f"Unknown tool: {name}")

    def _authorize(self, name: str) -> None:
        department = self.department
        if department is None:
            return
        can_use_tool = getattr(department, "can_use_tool", None)
        if not callable(can_use_tool) or can_use_tool(name):
            return
        raise ToolPermissionError(
            department=getattr(department, "name", "unknown"),
            tool_name=name,
            allowed_tools=list(getattr(department, "allowed_tools", []) or []),
        )
