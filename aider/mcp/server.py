from __future__ import annotations

import asyncio
import importlib.util
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory

HeadlessTaskHandler = Callable[[str], Awaitable[Any] | Any]
CompanyTaskHandler = Callable[[CompanyTask], Awaitable[Any] | Any]


@dataclass(frozen=True)
class BuiltinMCPTool:
    name: str
    permission_level: str
    description: str


BUILTIN_MCP_TOOLS: tuple[BuiltinMCPTool, ...] = (
    BuiltinMCPTool(
        "list_status",
        "read_only",
        "Return basic MCP server readiness and project status.",
    ),
    BuiltinMCPTool(
        "list_context_memory", "read_only", "Inspect project context memory."
    ),
    BuiltinMCPTool("list_approvals", "read_only", "List pending approval gates."),
    BuiltinMCPTool(
        "resolve_approval",
        "requires_approval",
        "Resolve an existing human approval gate.",
    ),
    BuiltinMCPTool(
        "submit_headless_task", "requires_approval", "Submit a headless Aider task."
    ),
    BuiltinMCPTool(
        "submit_company_task",
        "requires_approval",
        "Submit work into the Company orchestrator.",
    ),
    BuiltinMCPTool("list_skills", "read_only", "List approved procedural skills."),
    BuiltinMCPTool("get_skill", "read_only", "Read one approved skill by name."),
    BuiltinMCPTool(
        "list_pending_skill_proposals",
        "read_only",
        "List pending approval-gated skill proposals.",
    ),
    BuiltinMCPTool(
        "approve_skill_proposal",
        "requires_approval",
        "Open/route approval for a pending skill proposal.",
    ),
    BuiltinMCPTool(
        "get_recent_daemon_runs", "read_only", "Inspect recent Company daemon runs."
    ),
    BuiltinMCPTool(
        "trigger_daemon_run",
        "requires_approval",
        "Request a daemon run for an issue id.",
    ),
    BuiltinMCPTool(
        "get_knowledge_overview",
        "read_only",
        "Inspect institutional knowledge counts and summaries.",
    ),
    BuiltinMCPTool(
        "search_knowledge", "read_only", "Search local institutional knowledge."
    ),
    BuiltinMCPTool(
        "get_company_status",
        "read_only",
        "Return Company status without mutating state.",
    ),
)


def list_builtin_mcp_tools() -> list[dict[str, str]]:
    return [tool.__dict__.copy() for tool in BUILTIN_MCP_TOOLS]


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

    @property
    def repo_path(self) -> Path:
        return Path(self.project_memory.repo_path)

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

    def get_company_status(self) -> dict[str, Any]:
        return self.list_status()

    def list_context_memory(self) -> dict[str, Any]:
        data = self.project_memory.data
        return {
            "repo_path": self.project_memory.repo_path,
            "current_project_phase": data.get("current_project_phase"),
            "project_id": data.get("project_id"),
            "playbook": data.get("playbook", {}),
            "observability": data.get("observability", {}),
        }

    def list_skills(self) -> dict[str, Any]:
        if self.orchestrator is not None and hasattr(self.orchestrator, "state"):
            from aider.company.skills import CompanySkillManager

            manager = CompanySkillManager(
                self.orchestrator.state,
                getattr(self.orchestrator.company_config, "skill_learning", None),
            )
            return manager.inspect_skills()
        skills = []
        root = self.repo_path / ".aider" / "skills"
        for path in sorted(root.glob("*/*/SKILL.md")):
            skills.append(
                {
                    "scope": path.parent.parent.name,
                    "name": path.parent.name,
                    "path": str(path),
                    "description": self._first_heading(path),
                }
            )
        return {
            "enabled": True,
            "root": str(root),
            "available_count": len(skills),
            "available": skills,
            "recently_used": [],
            "pending_proposals": self.list_pending_skill_proposals()[
                "pending_proposals"
            ],
        }

    def get_skill(self, name: str) -> dict[str, Any]:
        for item in self.list_skills().get("available", []):
            if name in {item.get("name"), f"{item.get('scope')}/{item.get('name')}"}:
                path = Path(str(item.get("path", "")))
                content = path.read_text(encoding="utf-8") if path.exists() else ""
                return {**item, "content": content}
        return {"status": "not_found", "name": name}

    def list_pending_skill_proposals(self) -> dict[str, Any]:
        if self.orchestrator is not None and hasattr(self.orchestrator, "state"):
            from aider.company.skills import CompanySkillManager

            manager = CompanySkillManager(
                self.orchestrator.state,
                getattr(self.orchestrator.company_config, "skill_learning", None),
            )
            return {
                "pending_proposals": [
                    proposal.to_dict()
                    for proposal in manager.list_proposals(status="pending")
                ]
            }
        proposals = []
        for path in sorted(
            (self.repo_path / ".aider" / "skill_proposals").glob("*/*.json")
        ):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("status", "pending") == "pending":
                proposals.append(payload)
        return {"pending_proposals": proposals}

    async def approve_skill_proposal(
        self, id: str, feedback: str | None = None
    ) -> dict[str, Any]:
        task = self._approval_task(
            f"skill-proposal-{id}",
            "skill_proposal_approval",
            {"proposal_id": id, "feedback": feedback or ""},
        )
        decision = await self._request_human_approval(task)
        if not decision.get("approved"):
            return {"status": "rejected", "proposal_id": id, "feedback": feedback}
        if self.orchestrator is not None and hasattr(self.orchestrator, "state"):
            from aider.company.knowledge import KnowledgeManager

            result = KnowledgeManager(
                self.orchestrator.state,
                getattr(self.orchestrator.company_config, "skill_learning", None),
            ).approve_skill_proposal(id)
            return {"status": "approved", "proposal": result}
        return {"status": "approval_recorded", "proposal_id": id, "feedback": feedback}

    def get_recent_daemon_runs(self) -> dict[str, Any]:
        runs = self.project_memory.data.get("daemon_runs", [])
        if not isinstance(runs, list):
            runs = []
        return {"recent_daemon_runs": runs[-10:]}

    async def trigger_daemon_run(self, issue_id: str) -> dict[str, Any]:
        task = self._approval_task(
            f"daemon-run-{issue_id}",
            "daemon_run_approval",
            {"issue_id": issue_id},
        )
        decision = await self._request_human_approval(task)
        if not decision.get("approved"):
            return {"status": "rejected", "issue_id": issue_id}
        daemon = (
            getattr(self.orchestrator, "daemon", None) if self.orchestrator else None
        )
        if daemon is not None and hasattr(daemon, "run_issue"):
            result = daemon.run_issue(issue_id)
            if hasattr(result, "__await__"):
                result = await result
            return {
                "status": "triggered",
                "issue_id": issue_id,
                "result": self._serialize(result),
            }
        return {"status": "approval_recorded", "issue_id": issue_id}

    def get_knowledge_overview(self) -> dict[str, Any]:
        if self.orchestrator is not None and hasattr(self.orchestrator, "state"):
            from aider.company.knowledge import KnowledgeManager

            return KnowledgeManager(
                self.orchestrator.state,
                getattr(self.orchestrator.company_config, "skill_learning", None),
            ).get_overview()
        data = self.project_memory.data
        return {
            "playbook": data.get("playbook", {}),
            "skills": self.list_skills().get("available", []),
            "pending_proposals": self.list_pending_skill_proposals().get(
                "pending_proposals", []
            ),
            "counts": {
                "skills": self.list_skills().get("available_count", 0),
                "pending_proposals": len(
                    self.list_pending_skill_proposals().get("pending_proposals", [])
                ),
            },
        }

    def search_knowledge(self, query: str) -> dict[str, Any]:
        if self.orchestrator is not None and hasattr(self.orchestrator, "state"):
            from aider.company.knowledge import KnowledgeManager

            return {
                "results": KnowledgeManager(
                    self.orchestrator.state,
                    getattr(self.orchestrator.company_config, "skill_learning", None),
                ).search_knowledge(query)
            }
        terms = [term.lower() for term in str(query or "").split() if term]
        candidates = self.get_knowledge_overview()
        haystack = json.dumps(candidates, default=str, ensure_ascii=False).lower()
        return {
            "results": (
                [candidates]
                if terms and all(term in haystack for term in terms)
                else []
            )
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
        self, gate_id: str, *, approved: bool, reason: str | None = None
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
            raise RuntimeError(
                "Install the optional MCP dependencies to run the MCP server"
            )
        from mcp.server.fastmcp import FastMCP

        server = FastMCP(self.config.name)
        for name in [tool.name for tool in BUILTIN_MCP_TOOLS]:
            server.tool()(getattr(self, name))
        await server.run_stdio_async()

    def _approval_task(self, task_id: str, gate_name: str, payload: Any) -> CompanyTask:
        return CompanyTask(
            task_id=f"mcp-{task_id}-{uuid.uuid4().hex[:8]}",
            origin="mcp",
            target="engineering",
            artifact_type=gate_name,
            payload=payload,
            blocking=True,
            context={
                "gate_name": gate_name,
                "approver_role": "ceo",
                "artifact_preview": json.dumps(payload, default=str, sort_keys=True)[
                    :1500
                ],
                "handoff_to": "engineering",
            },
        )

    async def _request_human_approval(self, task: CompanyTask) -> dict[str, Any]:
        approvals = (
            getattr(self.orchestrator, "approvals", None) if self.orchestrator else None
        )
        if approvals is None or not hasattr(approvals, "create_request"):
            raise PermissionError("MCP write tool requires Company ApprovalManager")
        decision = await approvals.create_request(task)
        approvals.close_request(task.task_id)
        return {
            "approved": bool(getattr(decision, "approved", decision)),
            "reason": getattr(decision, "reason", None),
        }

    @staticmethod
    def _first_heading(path: Path) -> str:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("#"):
                    return line.strip("# ")
        except OSError:
            pass
        return ""

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
