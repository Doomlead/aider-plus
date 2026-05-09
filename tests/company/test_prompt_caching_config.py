from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aider.agent import loop as agent_loop_module
from aider.agent.loop import AiderAgentLoop
from aider.company.config import CompanyConfig, DepartmentConfig
from aider.company.department import Department
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyTask, Deliverable
from aider.memory import ProjectMemory


class DummyCoder:
    def __init__(self):
        self.main_model = SimpleNamespace(name="dummy-model", extra_params={})
        self.repo = None
        self.done_messages = []
        self.main_system = ""

    def clone(self, **kwargs):
        return self


class DummyDepartment(Department):
    name = "Product"

    async def process(self, task: CompanyTask) -> Deliverable:
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="dummy",
            payload="ok",
            status="success",
        )


def test_company_config_resolves_department_names_case_insensitively():
    config = CompanyConfig(
        default_enable_caching=False,
        departments={
            "Engineering": DepartmentConfig(
                name="engineering",
                enable_prompt_caching=True,
                preferred_model="preferred-model",
            )
        },
    )

    engineering = config.get_department_config("Engineering")
    qa = config.for_department("QA")

    assert engineering.enable_prompt_caching is True
    assert engineering.preferred_model == "preferred-model"
    assert qa.name == "QA"
    assert qa.enable_prompt_caching is False


def test_orchestrator_register_applies_department_config_without_agent_loop(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    config = CompanyConfig(
        departments={
            "product": DepartmentConfig(
                name="product",
                enable_prompt_caching=False,
                preferred_model="product-model",
            )
        }
    )
    orchestrator = CompanyOrchestrator(memory, company_config=config)
    department = DummyDepartment(memory)

    orchestrator.register(department)

    assert department.config.enable_prompt_caching is False
    assert department.config.preferred_model == "product-model"
    assert department._get_caching_enabled() is False


def test_agent_loop_run_structured_passes_cache_prompts_flag(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content="{}")
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])

    monkeypatch.setattr(agent_loop_module.litellm, "completion", fake_completion)
    loop = AiderAgentLoop(coder=DummyCoder(), enable_prompt_caching=False)

    async def run_test():
        await loop.run_structured(
            task="Review this change.",
            system_prompt="Return JSON.",
            enable_caching=True,
            model="override-model",
        )

    asyncio.run(run_test())

    assert calls[0]["model"] == "override-model"
    assert calls[0]["cache_prompts"] is True
    assert calls[0]["extra_body"] == {"cache_control": {"type": "ephemeral"}}
    assert calls[0]["tools"] is None
