from __future__ import annotations

import asyncio
import pytest

from aider.agent.loop import AiderAgentLoop
from aider.agent.tools import ToolPermissionError, ToolRegistry
from aider.company.schemas import ApprovalDecision
from aider.mcp import (
    AiderPlusMCPServer,
    AiderPlusMCPServerConfig,
    MCPClientManager,
    MCPConfig,
    MCPConnector,
    MCPServerConfig,
    MCPToolPolicy,
    MCPToolRef,
    mcp_tool_to_aider_tool,
)
from aider.memory import ProjectMemory


class FakeSession:
    async def call_tool(self, tool_name, arguments):
        return {"tool": tool_name, "arguments": arguments}


class FakeDepartment:
    name = "engineering"

    def __init__(self, allowed_tools):
        self.allowed_tools = allowed_tools

    def can_use_tool(self, tool_name):
        return tool_name in self.allowed_tools


class FakeManager(MCPClientManager):
    def __init__(self, config=None, approval_handler=None, approval_manager=None):
        super().__init__(
            config, approval_handler=approval_handler, approval_manager=approval_manager
        )
        self.server = MCPServerConfig(name="github", allowed_tools=["list_issues"])
        self.tool = MCPToolRef(
            server_name="github",
            name="list_issues",
            description="List issues",
            input_schema={
                "type": "object",
                "properties": {"state": {"type": "string"}},
            },
        )
        self.connector = MCPConnector(
            server=self.server,
            client=FakeSession(),
            session=FakeSession(),
            tools=[self.tool],
        )
        self._connectors_by_scope["test"] = [self.connector]


async def _execute(registry, name, arguments):
    return await registry.execute(name, arguments)


def test_mcp_config_interpolates_project_and_task_dirs():
    server = MCPServerConfig(
        name="docs",
        command="npx",
        args=["server", "${projectDir}", "${taskDir}"],
        env={"ROOT": "${projectDir}"},
    )

    resolved = server.interpolate(project_dir="/repo", task_dir="/repo/.aider/task")

    assert resolved.args == ["server", "/repo", "/repo/.aider/task"]
    assert resolved.env["ROOT"] == "/repo"


def test_mcp_config_normalizes_lists_and_rejects_unknown_transport():
    server = MCPServerConfig.from_dict(
        {
            "name": "docs",
            "args": "--stdio",
            "allowed_tools": "search",
            "tool_policies": {
                "search": {"allowed_departments": "engineering"},
            },
        }
    )

    assert server.transport == "stdio"
    assert server.args == ["--stdio"]
    assert server.allowed_tools == ["search"]
    assert server.tool_policies["search"].allowed_departments == ["engineering"]

    with pytest.raises(ValueError, match="Unsupported MCP transport"):
        MCPServerConfig.from_dict({"name": "bad", "transport": "websocket"})


def test_mcp_tools_convert_into_tool_registry_and_keep_department_allowlists():
    manager = FakeManager(MCPConfig(enabled=True))
    tool = mcp_tool_to_aider_tool(manager, manager.tool)
    registry = ToolRegistry(department=FakeDepartment([tool.name]))
    registry.register(tool)

    result = asyncio.run(_execute(registry, tool.name, {"state": "open"}))

    assert result["tool"] == "list_issues"
    assert result["arguments"] == {"state": "open"}

    blocked = ToolRegistry(department=FakeDepartment([]))
    blocked.register(tool)
    with pytest.raises(ToolPermissionError):
        blocked.execute(tool.name, {"state": "open"})


def test_mcp_manager_runs_approval_handler_for_gated_tools():
    approvals = []

    async def approve(request):
        approvals.append(request)
        return True

    manager = FakeManager(MCPConfig(enabled=True), approval_handler=approve)
    manager.tool.policy = MCPToolPolicy(requires_approval=True)

    result = asyncio.run(
        manager.call_tool("github", "list_issues", {"state": "closed"})
    )

    assert result["arguments"] == {"state": "closed"}
    assert approvals[0]["aider_tool"] == "mcp__github__list_issues"


def test_mcp_manager_skips_approval_handler_for_read_only_tools():
    approvals = []

    async def approve(request):
        approvals.append(request)
        return False

    manager = FakeManager(MCPConfig(enabled=True), approval_handler=approve)
    manager.tool.policy = MCPToolPolicy(read_only=True, requires_approval=False)

    result = asyncio.run(manager.call_tool("github", "list_issues", {"state": "open"}))

    assert result["arguments"] == {"state": "open"}
    assert approvals == []


def test_mcp_manager_denial_includes_audit_ready_reason():
    async def deny(request):
        return {"approved": False, "reason": "Needs maintenance window."}

    manager = FakeManager(MCPConfig(enabled=True), approval_handler=deny)
    manager.tool.policy = MCPToolPolicy(requires_approval=True)

    with pytest.raises(PermissionError, match="Denied MCP operation") as excinfo:
        asyncio.run(manager.call_tool("github", "list_issues", {"state": "closed"}))

    message = str(excinfo.value)
    assert "mcp__github__list_issues" in message
    assert "Needs maintenance window" in message


def test_mcp_manager_approval_manager_request_explains_why_tool_is_gated():
    class FakeApprovalManager:
        def __init__(self):
            self.tasks = []

        async def create_request(self, task):
            self.tasks.append(task)
            return ApprovalDecision(approved=True)

        def close_request(self, task_id):
            self.closed = task_id

    approvals = FakeApprovalManager()
    manager = FakeManager(MCPConfig(enabled=True), approval_manager=approvals)
    manager.tool.policy = MCPToolPolicy(requires_approval=True)

    asyncio.run(manager.call_tool("github", "list_issues", {"state": "closed"}))

    assert (
        approvals.tasks[0]
        .context["approval_reason"]
        .startswith("mcp__github__list_issues on MCP server github is marked")
    )


def test_non_aider_tool_calls_keep_original_arguments():
    assert AiderAgentLoop._tool_call_arguments(
        "mcp__github__list_issues", {"state": "open"}, 2
    ) == {"state": "open"}
    assert AiderAgentLoop._tool_call_arguments(
        "aider_coder", {"task": "edit", "constraints": "small"}, 2
    ) == {"task": "edit\n\nConstraints:\nsmall", "include_diff": False, "iteration": 2}


def test_safe_mcp_server_exposes_status_memory_and_approval_resolution(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    memory.update(
        {
            "project_id": "proj-1",
            "pending_approvals": [
                {
                    "task_id": "gate-1",
                    "gate_name": "mcp_tool_approval",
                    "status": "pending",
                }
            ],
        }
    )
    server = AiderPlusMCPServer(
        AiderPlusMCPServerConfig(repo_path=str(tmp_path)), project_memory=memory
    )

    assert server.list_status()["status"] == "ready"
    assert server.list_context_memory()["project_id"] == "proj-1"
    assert server.list_approvals()["pending_approvals"][0]["task_id"] == "gate-1"

    result = asyncio.run(server.resolve_approval("gate-1", approved=True))

    assert result == {"status": "resolved"}
    assert memory.data["pending_approvals"][0]["status"] == "approved"


def test_builtin_mcp_tool_listing_explains_approval_reasons():
    from aider.mcp.server import list_builtin_mcp_tools

    tools = {tool["name"]: tool for tool in list_builtin_mcp_tools()}

    assert tools["list_skills"]["permission_level"] == "read_only"
    assert "no shared approval gate" in tools["list_skills"]["approval_reason"]
    assert tools["trigger_daemon_run"]["permission_level"] == "requires_approval"
    assert "autonomous work" in tools["trigger_daemon_run"]["approval_reason"]


def test_safe_mcp_server_denied_mutating_tool_returns_audit_message(tmp_path):
    class DenyingApprovals:
        async def create_request(self, task):
            self.task = task
            return ApprovalDecision(approved=False, reason="Freeze window active.")

        def close_request(self, task_id):
            self.closed = task_id

    class Orchestrator:
        approvals = DenyingApprovals()

    server = AiderPlusMCPServer(
        AiderPlusMCPServerConfig(repo_path=str(tmp_path)), orchestrator=Orchestrator()
    )

    result = asyncio.run(server.trigger_daemon_run("AP-9"))

    assert result["status"] == "rejected"
    assert result["denial_reason"] == "Freeze window active."
    assert "Denied MCP operation" in result["audit_message"]
    assert "daemon_run_approval" in result["audit_message"]
    assert (
        Orchestrator.approvals.task.context["approval_reason"]
        == "Triggers autonomous work that may write code, comments, or status updates."
    )


def test_safe_mcp_server_submits_company_tasks(tmp_path):
    submitted = []

    async def handler(task):
        submitted.append(task)
        return {"ok": True}

    class ApprovingApprovals:
        async def create_request(self, task):
            self.task = task
            return ApprovalDecision(approved=True)

        def close_request(self, task_id):
            self.closed = task_id

    class Orchestrator:
        approvals = ApprovingApprovals()

    server = AiderPlusMCPServer(
        AiderPlusMCPServerConfig(repo_path=str(tmp_path)),
        orchestrator=Orchestrator(),
        company_handler=handler,
    )

    result = asyncio.run(server.submit_company_task("build it", target="product"))

    assert result["status"] == "submitted"
    assert (
        Orchestrator.approvals.task.context["approval_reason"]
        == "Submits new Company work and must be visible to governance/audit trails."
    )
    assert submitted[0].origin == "mcp"
    assert submitted[0].target == "product"
    assert submitted[0].payload == "build it"


def test_safe_mcp_server_lists_new_approval_aware_tools_and_knowledge(tmp_path):
    skill = tmp_path / ".aider" / "skills" / "engineering" / "ship-small"
    skill.mkdir(parents=True)
    skill.joinpath("SKILL.md").write_text(
        "# Ship Small\nKeep diffs reviewable.\n", encoding="utf-8"
    )
    proposal = tmp_path / ".aider" / "skill_proposals" / "engineering"
    proposal.mkdir(parents=True)
    proposal.joinpath("prop-1.json").write_text(
        '{"proposal_id":"prop-1","status":"pending","name":"ship-small"}',
        encoding="utf-8",
    )
    memory = ProjectMemory(str(tmp_path))
    memory.update({"daemon_runs": [{"issue_id": "AP-1", "status": "ok"}]})
    server = AiderPlusMCPServer(
        AiderPlusMCPServerConfig(repo_path=str(tmp_path)), project_memory=memory
    )

    skills = server.list_skills()
    assert skills["available_count"] == 1
    assert server.get_skill("engineering/ship-small")["content"].startswith(
        "# Ship Small"
    )
    assert (
        server.list_pending_skill_proposals()["pending_proposals"][0]["proposal_id"]
        == "prop-1"
    )
    assert (
        server.get_recent_daemon_runs()["recent_daemon_runs"][0]["issue_id"] == "AP-1"
    )
    assert server.get_knowledge_overview()["counts"]["skills"] == 1
    assert server.search_knowledge("Ship")["results"]
    assert server.get_company_status()["status"] == "ready"


def test_mcp_manager_registers_approval_aware_tool_policy():
    manager = FakeManager(MCPConfig(enabled=True))
    policy = manager.register_approval_aware_tool("list_issues", "requires_approval")

    assert policy.requires_approval is True
    assert manager.config.servers == {}

    with pytest.raises(ValueError):
        manager.register_approval_aware_tool("list_issues", "write")


def test_agent_loop_registers_builtin_mcp_discovery_tool():
    class DummyCoder:
        done_messages = []
        conversation_memory = None
        project_memory = None
        main_system = ""

        class MainModel:
            name = "test-model"
            extra_params = {}

        main_model = MainModel()
        repo = None

        def clone(self, **_kwargs):
            return self

    loop = AiderAgentLoop(coder=DummyCoder())

    assert "list_available_mcp_tools" in loop.tool_registry.tools
    assert any(
        tool["name"] == "trigger_daemon_run"
        for tool in asyncio.run(
            loop.tool_registry.execute("list_available_mcp_tools", {})
        )["tools"]
    )
