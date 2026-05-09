from aider.company.config import CompanyConfig, DepartmentConfig, default_company_config
from aider.company.departments.qa import QADepartment
from aider.company.orchestrator import CompanyOrchestrator
from aider.memory import ProjectMemory


def test_company_config_returns_department_specific_caching_settings():
    config = CompanyConfig(
        default_enable_caching=False,
        departments={
            "engineering": DepartmentConfig(
                name="engineering",
                enable_prompt_caching=True,
                preferred_model="example-model",
            )
        },
    )

    engineering = config.get_department_config("Engineering")
    qa = config.get_department_config("qa")

    assert engineering.enable_prompt_caching is True
    assert engineering.preferred_model == "example-model"
    assert qa.enable_prompt_caching is False
    assert qa.preferred_model is None


def test_orchestrator_applies_department_prompt_caching_config(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    config = default_company_config()
    orchestrator = CompanyOrchestrator(memory, company_config=config)
    qa = QADepartment(project_memory=memory)

    orchestrator.register(qa)

    assert qa._get_caching_enabled() is False
    assert qa.config is config.get_department_config("qa")
