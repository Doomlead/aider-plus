from __future__ import annotations

from types import SimpleNamespace

import asyncio

from aider.agent.loop import AgentLoopConfig
from aider.company.agent_factory import build_company_agent_loops
from aider.company.config import (
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


def test_nanobot_coo_persists_session_and_routes_to_department(tmp_path):
    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        department = EchoDepartment(memory)
        orchestrator.register(department)
        coo = NanobotCOO(orchestrator=orchestrator)

        deliverable = await coo.receive_user_message(
            prompt="build the thing",
            channel="cli",
            session_key="cli:test-user",
            target="product",
            context={"project_name": "demo"},
            task_id="task-1",
        )

        assert deliverable.task_id == "task-1"
        assert deliverable.department == "product"
        session = coo.session_manager.get_or_create("cli:test-user")
        assert session.metadata["last_target"] == "product"
        assert [message["role"] for message in session.messages] == ["user", "assistant"]
        assert coo.session_manager._path("cli:test-user").exists()

    asyncio.run(run())

def test_apply_agent_model_overrides_from_env(monkeypatch):
    monkeypatch.setenv("AIDER_COMPANY_AGENT_MODELS", "product=gpt-4o,ux=claude-3")
    monkeypatch.setenv("AIDER_COMPANY_MODEL_QA", "o3-mini")

    config = apply_agent_model_overrides_from_env(CompanyConfig())

    assert config.get_department_config("product").preferred_model == "gpt-4o"
    assert config.get_department_config("ux").preferred_model == "claude-3"
    assert config.get_department_config("qa").preferred_model == "o3-mini"
