from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DepartmentConfig:
    name: str
    enable_prompt_caching: bool = True
    preferred_model: str | None = None


@dataclass
class CompanyConfig:
    default_enable_caching: bool = True
    departments: dict[str, DepartmentConfig] = field(default_factory=dict)

    def get_department_config(self, name: str) -> DepartmentConfig:
        key = name.lower()
        return self.departments.get(
            key,
            DepartmentConfig(
                name=name,
                enable_prompt_caching=self.default_enable_caching,
            ),
        )


def default_company_config() -> CompanyConfig:
    return CompanyConfig(
        default_enable_caching=True,
        departments={
            "engineering": DepartmentConfig(
                name="engineering",
                enable_prompt_caching=True,
                preferred_model=None,
            ),
            "reviewer": DepartmentConfig(
                name="reviewer",
                enable_prompt_caching=True,
                preferred_model="claude-3-7-sonnet-20250219",
            ),
            "product": DepartmentConfig(name="product", enable_prompt_caching=True),
            "qa": DepartmentConfig(name="qa", enable_prompt_caching=False),
            "devops": DepartmentConfig(name="devops", enable_prompt_caching=False),
            "ux": DepartmentConfig(name="ux", enable_prompt_caching=True),
        },
    )
