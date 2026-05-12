from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aider.agent.loop import AgentLoopConfig
from aider.company.agents import DepartmentAgentFactory
from aider.company.config import CompanyConfig, DepartmentConfig, default_company_config
from aider.company.coo import COOAgentConfig, COOAgentKernel, COODepartment
from aider.company.schemas import CompanyTask, Deliverable
from aider.memory import ProjectMemory


class DummyCoder:
    def __init__(self, name="base-model"):
        self.main_model = SimpleNamespace(name=name, extra_params={})
        self.repo = None
        self.done_messages = []
        self.main_system = ""
        self.clones = []

    def clone(self, **kwargs):
        clone = DummyCoder(getattr(kwargs.get("main_model"), "name", self.main_model.name))
        self.clones.append(kwargs)
        return clone


class SubmitRecorder:
    def __init__(self, deliverable=None):
        self.tasks = []
        self.deliverable = deliverable

    async def __call__(self, task):
        self.tasks.append(task)
        return self.deliverable


def test_department_agent_factory_creates_isolated_model_configured_loops(monkeypatch):
    monkeypatch.setattr("aider.company.agents.models.Model", lambda name: SimpleNamespace(name=name))
    coder = DummyCoder()
    config = CompanyConfig(
        departments={
            "product": DepartmentConfig(name="product", preferred_model="product-model"),
            "ux": DepartmentConfig(name="ux", preferred_model="ux-model"),
        }
    )
    factory = DepartmentAgentFactory(
        coder=coder,
        company_config=config,
        base_config=AgentLoopConfig(use_architect_mode=True),
    )

    product_loop = factory.create("product")
    ux_loop = factory.create("ux")

    assert product_loop is not ux_loop
    assert product_loop.tool_registry is not ux_loop.tool_registry
    assert product_loop.default_model == "product-model"
    assert ux_loop.default_model == "ux-model"
    assert product_loop.config.department_config.preferred_model == "product-model"
    assert ux_loop.config.architect_model == "ux-model"


def test_default_company_config_applies_user_agent_model_overrides(monkeypatch):
    monkeypatch.setenv("AIDER_AGENT_MODELS", "product=model-a,ux=model-b")
    monkeypatch.setenv("AIDER_AGENT_MODEL_ENGINEERING", "model-c")

    config = default_company_config()

    assert config.get_department_config("product").preferred_model == "model-a"
    assert config.get_department_config("ux").preferred_model == "model-b"
    assert config.get_department_config("engineering").preferred_model == "model-c"


def test_coo_agent_kernel_routes_and_records_bounded_session_memory(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    agent = COOAgentKernel(config=COOAgentConfig(channel="phase-1", max_session_messages=3))
    routed = Deliverable(
        task_id="task-1-product",
        department="product",
        artifact_type="prd",
        payload={"ok": True},
        status="success",
    )
    recorder = SubmitRecorder(routed)
    coo = COODepartment(memory, agent=agent)
    coo._submit_task = recorder

    async def run_test():
        return await coo.process(
            CompanyTask(
                task_id="task-1",
                origin="ceo",
                target="coo",
                artifact_type="raw_prompt",
                payload={"target_department": "product", "payload": "Build it"},
                blocking=False,
                context={},
            )
        )

    deliverable = asyncio.run(run_test())

    assert deliverable.department == "coo"
    assert deliverable.payload["content"]["routed_to"] == "product"
    assert deliverable.payload["channel"] == "phase-1"
    assert recorder.tasks[0].origin == "coo"
    assert recorder.tasks[0].target == "product"
    assert recorder.tasks[0].context["coo_message"]["recipient"] == "product"
    assert len(agent.memory.as_dicts()) == 2


def test_coo_routing_policy_uses_intent_when_no_explicit_target(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    recorder = SubmitRecorder()
    coo = COODepartment(memory, agent=COOAgentKernel())
    coo._submit_task = recorder

    async def run_test():
        return await coo.process(
            CompanyTask(
                task_id="task-2",
                origin="ceo",
                target="coo",
                artifact_type="raw_prompt",
                payload="Please fix the login bug in code",
                blocking=False,
                context={},
            )
        )

    deliverable = asyncio.run(run_test())

    assert deliverable.payload["content"]["routed_to"] == "engineering"
    assert recorder.tasks[0].target == "engineering"
