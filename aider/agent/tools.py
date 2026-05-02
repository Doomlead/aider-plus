from dataclasses import dataclass
from typing import Any, Callable, Dict


@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    parameters: Dict


class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, Tool] = {}

    def register(self, tool: Tool):
        self.tools[tool.name] = tool

    def get_tool_definitions(self) -> list:
        """Return list of tool schemas for LLM tool calling."""
        return [t.parameters for t in self.tools.values()]

    def execute(self, name: str, arguments: dict) -> Any:
        if name in self.tools:
            return self.tools[name].func(**arguments)
        raise ValueError(f"Unknown tool: {name}")
