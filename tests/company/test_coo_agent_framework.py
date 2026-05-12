from __future__ import annotations

from types import SimpleNamespace

import asyncio

from aider.agent.loop import AgentLoopConfig, AiderAgentLoop
from aider.company.agent_factory import (
    build_agent_loop_for_role,
    build_company_agent_loops,
)
from aider.company.config import (
    AgentConfig,
    CompanyConfig,
    DepartmentConfig,
    apply_agent_model_overrides_from_env,
)
from aider.company.coo import NanobotCOO
from aider.company.department import Department
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyTask, Deliverable
from aider.memory import ProjectMemory


class DummyCoder:
    def __init__(self, name="base", clones=None):
        self.main_model = SimpleNamespace(name=name, extra_params={})
        self.done_messages = []
        self.repo = None
        self.root = "."
        self.clones = clones if clones is not None else []

    def clone(self, **kwargs):
        model = kwargs.get("main_model")
        clone = DummyCoder(getattr(model, "name", self.main_model.name), self.clones)
        self.clones.append((clone, kwargs))
        return clone


class EchoDepartment(Department):
    name = "product"

    def __init__(self, memory, name="product"):
        super().__init__(memory)
        self.name = name

    async def process(self, task: CompanyTask) -> Deliverable:
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="echo",
            payload={"prompt": task.payload, "context": task.context},
            status="success",
            metadata={},
        )


def test_build_company_agent_loops_creates_dedicated_loop_per_agent(tmp_path):
    coder = DummyCoder()
    config = CompanyConfig(
        departments={
            "product": DepartmentConfig(name="product", preferred_model="gpt-4o"),
            "coo": DepartmentConfig(name="coo", preferred_model="claude-sonnet-4-5"),
        }
    )

    loops = build_company_agent_loops(
        coder=coder,
        company_config=config,
        base_config=AgentLoopConfig(use_architect_mode=True),
    )

    assert set(loops) == {"coo", "product", "ux", "engineering", "qa", "devops"}
    assert loops["product"] is not loops["engineering"]
    assert loops["product"].coder is not loops["engineering"].coder
    assert loops["product"].coder.main_model.name == "gpt-4o"
    assert loops["coo"].coder.main_model.name == "claude-sonnet-4-5"


def test_build_agent_loop_for_role_respects_caching_flag():
    coder = DummyCoder()
    agent_config = AgentConfig(
        name="ux",
        enable_caching=False,
        cache_type="none",
        preferred_model="gpt-4o",
    )

    loop = build_agent_loop_for_role("ux", agent_config, coder=coder)

    assert isinstance(loop, AiderAgentLoop)
    assert loop.config.enable_caching is False
    assert loop.config.cache_type == "none"
    assert loop.enable_prompt_caching is False
    assert loop.coder.main_model.name == "gpt-4o"


def test_nanobot_coo_persists_session_and_routes_to_department(tmp_path):
    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        department = EchoDepartment(memory)
        orchestrator.register(department)
        coo = NanobotCOO(orchestrator=orchestrator)

        result = await coo.receive_user_message(
            message="build the thing",
            session_id="cli:test-user",
            surface="cli",
            target="product",
            context={"project_name": "demo"},
            task_id="task-1",
        )
        deliverable = result["deliverable"]

        assert result["task_id"] == "task-1"
        assert result["target"] == "product"
        assert result["events"]
        assert deliverable.task_id == "task-1"
        assert deliverable.department == "product"
        session = coo.session_manager.get_or_create("cli:test-user")
        assert session.metadata["last_target"] == "product"
        assert [message["role"] for message in session.messages] == [
            "user",
            "assistant",
        ]
        assert coo.session_manager._path("cli:test-user").exists()

    asyncio.run(run())


def test_apply_agent_model_overrides_from_env(monkeypatch):
    monkeypatch.setenv("AIDER_COMPANY_AGENT_MODELS", "product=gpt-4o,ux=claude-3")
    monkeypatch.setenv("AIDER_COMPANY_MODEL_QA", "o3-mini")

    config = apply_agent_model_overrides_from_env(CompanyConfig())

    assert config.get_department_config("product").preferred_model == "gpt-4o"
    assert config.get_department_config("ux").preferred_model == "claude-3"
    assert config.get_department_config("qa").preferred_model == "o3-mini"


def test_nanobot_coo_uses_llm_route_when_enabled(tmp_path):
    class RoutingLoop:
        def __init__(self):
            self.calls = []

        async def run_structured(self, **kwargs):
            self.calls.append(kwargs)
            return {"content": '{"target": "ux", "reason": "Needs design"}'}

    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        product = EchoDepartment(memory, name="product")
        ux = EchoDepartment(memory, name="ux")
        orchestrator.register(product)
        orchestrator.register(ux)
        loop = RoutingLoop()
        coo = NanobotCOO(
            orchestrator=orchestrator,
            coo_agent_loop=loop,
            enable_llm_routing=True,
        )

        result = await coo.receive_user_message(
            "Create a wireframe for onboarding",
            "cli:llm-route",
            surface="cli",
            task_id="task-llm",
        )

        assert loop.calls
        assert result["route"]["strategy"] == "llm"
        assert result["target"] == "ux"
        assert result["deliverable"].department == "ux"
        session = coo.session_manager.get_or_create("cli:llm-route")
        assert session.metadata["last_route"]["strategy"] == "llm"

    asyncio.run(run())


def test_nanobot_coo_session_status_includes_formatted_events(tmp_path):
    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        department = EchoDepartment(memory, name="engineering")
        orchestrator.register(department)
        coo = NanobotCOO(orchestrator=orchestrator, default_target="engineering")

        await coo.receive_user_message(
            message="implement session status",
            session_id="cli:status",
            surface="cli",
            task_id="task-status",
        )

        formatted_events = coo.bus.get_formatted_events(limit=5)
        status = await coo.get_session_status("cli:status")

        assert formatted_events
        assert any("cli:status" in event for event in formatted_events)
        assert status["status"] == "active"
        assert status["active_department"] == "engineering"
        assert status["current_route"]["target"] == "engineering"
        assert status["recent_events"] == coo.bus.snapshot()["formatted_events"]
        assert status["metrics"]["message_count"] == 2
        assert status["session"]["route_history"][-1]["target"] == "engineering"
        assert status["session"]["last_deliverable_summary"]

    asyncio.run(run())


def test_nanobot_coo_falls_back_after_llm_retry_exhaustion(tmp_path):
    class FailingRoutingLoop:
        def __init__(self):
            self.calls = 0

        async def run_structured(self, **kwargs):
            self.calls += 1
            raise RuntimeError("rate limit")

    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        orchestrator.register(EchoDepartment(memory, name="engineering"))
        loop = FailingRoutingLoop()
        coo = NanobotCOO(
            orchestrator=orchestrator,
            coo_agent_loop=loop,
            enable_llm_routing=True,
            default_target="engineering",
        )

        result = await coo.receive_user_message(
            "implement retry fallback",
            "cli:llm-fallback",
            surface="cli",
            task_id="task-llm-fallback",
        )
        status = await coo.get_session_status("cli:llm-fallback")
        error_events = [
            event for event in result["events"] if event["event_type"] == "coo_error"
        ]

        assert loop.calls == 4
        assert result["route"]["strategy"] == "deterministic"
        assert result["target"] == "engineering"
        assert error_events
        assert (
            error_events[-1]["metadata"]["message_metadata"]["error_type"]
            == "llm_route_failed"
        )
        assert error_events[-1]["metadata"]["message_metadata"]["retries"] == 3
        assert status["error_count"] == 1
        assert status["last_error"]["error_type"] == "llm_route_failed"

    asyncio.run(run())


def test_nanobot_coo_successful_llm_retry(tmp_path):
    class FlakyRoutingLoop:
        def __init__(self):
            self.calls = 0

        async def run_structured(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary orchestrator hiccup")
            return {
                "content": (
                    '{"reasoning": "Needs tests", "chosen_department": "qa", '
                    '"confidence": 0.82, "should_escalate_to_human": false}'
                )
            }

    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        orchestrator.register(EchoDepartment(memory, name="engineering"))
        orchestrator.register(EchoDepartment(memory, name="qa"))
        loop = FlakyRoutingLoop()
        coo = NanobotCOO(
            orchestrator=orchestrator,
            coo_agent_loop=loop,
            enable_llm_routing=True,
            default_target="engineering",
        )

        result = await coo.receive_user_message(
            "write regression tests",
            "cli:llm-retry",
            surface="cli",
            task_id="task-llm-retry",
        )
        status = await coo.get_session_status("cli:llm-retry")

        assert loop.calls == 2
        assert result["target"] == "qa"
        assert result["route"]["strategy"] == "llm"
        assert result["route"]["confidence"] == 0.82
        assert not [
            event for event in result["events"] if event["event_type"] == "coo_error"
        ]
        assert status["error_count"] == 0

    asyncio.run(run())


def test_nanobot_coo_routing_prompt_contains_structured_context(tmp_path):
    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        orchestrator.register(EchoDepartment(memory, name="product"))
        orchestrator.register(EchoDepartment(memory, name="engineering"))
        coo = NanobotCOO(orchestrator=orchestrator, default_target="engineering")
        session = coo.session_manager.get_or_create("cli:prompt")
        session.metadata["last_target"] = "engineering"
        session.metadata["last_route"] = {
            "target": "engineering",
            "strategy": "deterministic",
            "reasoning": "Existing work",
        }

        prompt = coo._get_coo_routing_prompt(session)

        assert "Current project phase:" in prompt
        assert "Recent route history (last 3 decisions):" in prompt
        assert "Active department: engineering" in prompt
        assert "Available departments list:" in prompt
        assert "product" in prompt and "engineering" in prompt
        assert "COORouteDecision schema" in prompt
        assert '"reasoning"' in prompt
        assert '"chosen_department"' in prompt
        assert '"confidence"' in prompt
        assert '"should_escalate_to_human"' in prompt

    asyncio.run(run())
