import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, get_args, get_origin


@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    parameters: Dict | None = None
    permission_tier: str = "safe"
    timeout_seconds: float = 120.0
    artifact_type: str = "text"
    auto_schema: bool = True
    required_reaction: str | None = None


def _json_schema_for_annotation(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Signature.empty:
        return {"type": "string"}

    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return {"type": "array", "items": _json_schema_for_annotation(args[0] if args else Any)}
    if origin is dict:
        return {"type": "object"}
    if origin in (tuple, set):
        return {"type": "array"}

    if annotation is str:
        return {"type": "string"}
    if annotation in (int,):
        return {"type": "integer"}
    if annotation in (float,):
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    return {"type": "string"}


def _build_tool_schema(tool: Tool) -> dict[str, Any]:
    if tool.parameters:
        return tool.parameters

    sig = inspect.signature(tool.func)
    required = []
    properties: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        properties[pname] = _json_schema_for_annotation(param.annotation)
        if param.default is inspect.Signature.empty:
            required.append(pname)

    doc = inspect.getdoc(tool.func) or tool.description
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": doc,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


@dataclass
class ToolResult:
    status: str
    data: Any = None
    artifact_type: str = "text"
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "data": self.data,
            "artifact_type": self.artifact_type,
        }
        if self.error:
            payload["error"] = self.error
        if self.meta:
            payload["meta"] = self.meta
        return payload


class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, Tool] = {}

    def register(self, tool: Tool):
        if tool.permission_tier not in {"safe", "risky"}:
            raise ValueError(f"Unsupported permission tier for tool {tool.name}: {tool.permission_tier}")
        self.tools[tool.name] = tool

    def get_tool_definitions(self) -> list:
        """Return list of tool schemas for LLM tool calling."""
        return [_build_tool_schema(t) for t in self.tools.values()]

    async def execute(self, name: str, arguments: dict, *, allow_risky: bool = False) -> ToolResult:
        tool = self.tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")

        if tool.permission_tier == "risky" and not allow_risky:
            return ToolResult(
                status="error",
                artifact_type=tool.artifact_type,
                error="Tool requires explicit approval",
                meta={"permission_tier": "risky", "required_reaction": tool.required_reaction or "approve_tool"},
            )

        try:
            call = tool.func(**arguments)
            if inspect.isawaitable(call):
                value = await asyncio.wait_for(call, timeout=tool.timeout_seconds)
            else:
                value = await asyncio.wait_for(asyncio.to_thread(lambda: call), timeout=tool.timeout_seconds)
        except asyncio.TimeoutError:
            return ToolResult(status="error", artifact_type=tool.artifact_type, error=f"Timed out after {tool.timeout_seconds}s")
        except Exception as exc:
            return ToolResult(status="error", artifact_type=tool.artifact_type, error=str(exc))

        if isinstance(value, ToolResult):
            if not value.artifact_type:
                value.artifact_type = tool.artifact_type
            return value

        return ToolResult(status="ok", data=value, artifact_type=tool.artifact_type)
