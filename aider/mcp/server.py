from __future__ import annotations

import asyncio
import importlib.util
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory

HeadlessTaskHandler = Callable[[str], Awaitable[Any] | Any]
CompanyTaskHandler = Callable[[CompanyTask], Awaitable[Any] | Any]


@dataclass
class AiderPlusMCPServerConfig:
    repo_path: str
    name: str = "aider-plus"
    version: str = "0.1.0"


class AiderPlusMCPServer:
    """Small, safe MCP server façade for Aider Plus.

    The class is usable directly in tests and can also be bound to the optional
    MCP SDK stdio transport via ``run_stdio``.
    """

    def __init__(
        self,
        config: AiderPlusMCPServerConfig,
        *,
        project_memory: ProjectMemory | None = None,
        orchestrator: Any | None = None,
        headless_handler: HeadlessTaskHandler | None = None,
        company_handler: CompanyTaskHandler | None = None,
    ):
        self.config = config
        self.project_memory = project_memory or ProjectMemory(config.repo_path)
        self.orchestrator = orchestrator
        self.headless_handler = headless_handler
        self.company_handler = company_handler

    def list_status(self) -> dict[str, Any]:
        if self.orchestrator is not None and hasattr(
            self.orchestrator, "company_status"
        ):
            return {"status": self.orchestrator.company_status()}
        return {
            "status": "ready",
            "repo_path": self.project_memory.repo_path,
            "current_project_phase": self.project_memory.data.get(
                "current_project_phase"
            ),
        }

    def list_context_memory(self) -> dict[str, Any]:
        data = self.project_memory.data
        return {
            "repo_path": self.project_memory.repo_path,
            "current_project_phase": data.get("current_project_phase"),
            "project_id": data.get("project_id"),
            "playbook": data.get("playbook", {}),
            "observability": data.get("observability", {}),
        }

    async def submit_headless_task(self, prompt: str) -> dict[str, Any]:
        if self.headless_handler is None:
            raise RuntimeError("No headless task handler is configured")
        result = self.headless_handler(prompt)
        if hasattr(result, "__await__"):
            result = await result
        return {"status": "submitted", "result": self._serialize(result)}

    async def submit_company_task(
        self,
        prompt: str,
        *,
        target: str = "engineering",
        artifact_type: str = "raw_prompt",
    ) -> dict[str, Any]:
        task = CompanyTask(
            task_id=f"mcp-{uuid.uuid4().hex[:12]}",
            origin="mcp",
            target=target,
            artifact_type=artifact_type,
            payload=prompt,
            blocking=False,
            context={"source": "mcp_server"},
        )
        handler = self.company_handler or (
            self.orchestrator.submit if self.orchestrator else None
        )
        if handler is None:
            raise RuntimeError("No company task handler is configured")
        result = handler(task)
        if hasattr(result, "__await__"):
            result = await result
        return {
            "status": "submitted",
            "task_id": task.task_id,
            "result": self._serialize(result),
        }

    def list_approvals(self) -> dict[str, Any]:
        if self.orchestrator is not None and hasattr(self.orchestrator, "state"):
            approvals = self.orchestrator.state.get_pending_approvals()
        else:
            approvals = self.project_memory.data.get("pending_approvals", [])
        return {"pending_approvals": approvals if isinstance(approvals, list) else []}

    async def resolve_approval(
        self,
        gate_id: str,
        *,
        approved: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if self.orchestrator is not None and hasattr(
            self.orchestrator, "handle_approval_response"
        ):
            changed = await self.orchestrator.handle_approval_response(
                gate_id,
                approved,
                source="mcp",
                reason=reason,
                metadata={"source": "mcp_server"},
            )
            return {
                "status": "resolved" if changed else "not_found_or_already_resolved"
            }
        approvals = self.project_memory.data.get("pending_approvals", [])
        if not isinstance(approvals, list):
            approvals = []
        for approval in approvals:
            if isinstance(approval, dict) and str(approval.get("task_id")) == gate_id:
                approval["status"] = "approved" if approved else "rejected"
                approval["cli_resolution"] = {
                    "action": "approve" if approved else "reject",
                    "reason": reason
                    or ("Approved via MCP" if approved else "Rejected via MCP"),
                    "source": "mcp",
                }
                self.project_memory.update({"pending_approvals": approvals})
                self.project_memory.persist()
                return {"status": "resolved"}
        return {"status": "not_found"}

    async def run_stdio(self) -> None:  # pragma: no cover - requires optional MCP SDK
        if importlib.util.find_spec("mcp") is None:
            raise RuntimeError("Install the optional MCP dependencies to run the MCP server")
        from mcp.server.fastmcp import FastMCP

        server = FastMCP(self.config.name)
        server.tool()(self.list_status)
        server.tool()(self.list_context_memory)
        server.tool()(self.submit_headless_task)
        server.tool()(self.submit_company_task)
        server.tool()(self.list_approvals)
        server.tool()(self.resolve_approval)
        await server.run_stdio_async()

    @staticmethod
    def _serialize(value: Any) -> Any:
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if isinstance(value, (str, int, float, bool, list, dict)) or value is None:
            return value
        return str(value)


def run_stdio_server(
    repo_path: str | None = None,
) -> None:  # pragma: no cover - CLI helper
    import os
    import sys

    resolved_repo = repo_path or (sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
    asyncio.run(
        AiderPlusMCPServer(
            AiderPlusMCPServerConfig(repo_path=resolved_repo)
        ).run_stdio()
    )
