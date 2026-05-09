"""
Company-level configuration dataclasses.

These are entirely separate from Aider's core config (aider/config.py).
They configure orchestration behaviour — department settings, caching,
model preferences — without touching any Aider internal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class DepartmentConfig:
    """
    Per-department runtime configuration.

    Attributes:
        name: Department identifier (matches Department.name).
        enable_prompt_caching: Whether to pass cache_control options in API
            calls made by this department. Default True.
        preferred_model: Optional model override for this department's agent
            loop calls. None means use the agent loop default.
    """

    name: str
    enable_prompt_caching: bool = True
    preferred_model: Optional[str] = None


@dataclass
class CompanyConfig:
    """
    Top-level company orchestration configuration.

    Attributes:
        departments: Per-department overrides. Departments not listed here
            receive default DepartmentConfig values.
        default_enable_caching: Fallback caching flag for departments without
            an explicit DepartmentConfig entry.
        record_caching_stats: Whether to write cache run counts into the
            observability section of project memory.
    """

    default_enable_caching: bool = True
    departments: Dict[str, DepartmentConfig] = field(default_factory=dict)
    record_caching_stats: bool = True

    def get_department_config(self, name: str) -> DepartmentConfig:
        """
        Return the DepartmentConfig for *name*, falling back to company defaults.

        Department keys are matched case-insensitively so callers can use
        user-facing labels without having to normalize them first.
        """
        key = name.lower()
        if key in self.departments:
            return self.departments[key]
        for dept_name, dept_config in self.departments.items():
            if dept_name.lower() == key:
                return dept_config
        return DepartmentConfig(
            name=name,
            enable_prompt_caching=self.default_enable_caching,
        )

    def for_department(self, name: str) -> DepartmentConfig:
        """Return the DepartmentConfig for *name* (backwards-compatible alias)."""
        return self.get_department_config(name)


def default_company_config() -> CompanyConfig:
    """
    Return the recommended CompanyConfig for production use.

    Engineering and reviewer benefit most from caching because they use large,
    stable prompts and repo context. QA and DevOps prompts are typically smaller
    and short-lived, so caching overhead is not enabled by default there.
    """
    return CompanyConfig(
        departments={
            "engineering": DepartmentConfig(
                name="engineering",
                enable_prompt_caching=True,
                preferred_model="claude-sonnet-4-5",
            ),
            "reviewer": DepartmentConfig(
                name="reviewer",
                enable_prompt_caching=True,
                preferred_model="claude-sonnet-4-5",
            ),
            "product": DepartmentConfig(
                name="product",
                enable_prompt_caching=True,
            ),
            "ux": DepartmentConfig(
                name="ux",
                enable_prompt_caching=True,
            ),
            "qa": DepartmentConfig(
                name="qa",
                enable_prompt_caching=False,
            ),
            "devops": DepartmentConfig(
                name="devops",
                enable_prompt_caching=False,
            ),
        },
        default_enable_caching=True,
        record_caching_stats=True,
    )
