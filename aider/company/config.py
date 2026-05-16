"""
Company-level configuration dataclasses.

These are entirely separate from Aider's core config (aider/config.py).
They configure orchestration behaviour — department settings, caching,
model preferences — without touching any Aider internal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

from aider.mcp.config import MCPConfig
from aider.company.skills import SkillLearningConfig


@dataclass
class AgentConfig:
    """
    Per-department runtime configuration.

    Attributes:
        name: Department identifier (matches Department.name).
        enable_caching: Whether to pass cache_control options in API
            calls made by this agent. Default True.
        cache_type: Prompt-caching strategy. "auto" lets the agent loop use
            native Aider/litellm support, "prompt" forces prompt caching, and
            "none" disables caching.
        preferred_model: Optional model override for this department's agent
            loop calls. None means use the agent loop default.
        max_review_iterations: Optional cap for reviewer/programmer revision
            cycles before forced approval. None means use the department default.
    """

    name: str
    enable_caching: bool = True
    cache_type: Literal["auto", "prompt", "none"] = "auto"
    preferred_model: Optional[str] = None
    max_review_iterations: Optional[int] = None
    enable_prompt_caching: Optional[bool] = None
    devops_build_fallback_commands: list[str] = field(default_factory=list)
    devops_retry_attempts: int = 3
    devops_retry_base_delay: float = 0.25
    devops_log_capture_dir: str = ".aider/company/build-logs"

    def __post_init__(self) -> None:
        if self.enable_prompt_caching is not None:
            self.enable_caching = bool(self.enable_prompt_caching)
        if self.cache_type == "none":
            self.enable_caching = False
        self.enable_prompt_caching = self.enable_caching
        self.devops_retry_attempts = max(1, int(self.devops_retry_attempts or 1))
        self.devops_retry_base_delay = max(0.0, float(self.devops_retry_base_delay or 0.0))


class DepartmentConfig(AgentConfig):
    """Backward-compatible name for per-department agent configuration."""


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
        enable_coo_llm_routing: Whether the COO may use its agent loop to
            classify user requests before handing them to a department.
    """

    default_enable_caching: bool = True
    departments: Dict[str, DepartmentConfig] = field(default_factory=dict)
    record_caching_stats: bool = True
    enable_coo_llm_routing: bool = False
    mcp: MCPConfig = field(default_factory=MCPConfig)
    skill_learning: SkillLearningConfig = field(default_factory=SkillLearningConfig)

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
            enable_caching=self.default_enable_caching,
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
            "coo": DepartmentConfig(
                name="coo",
                enable_caching=True,
            ),
            "engineering": DepartmentConfig(
                name="engineering",
                enable_caching=True,
                preferred_model="claude-sonnet-4-5",
            ),
            "reviewer": DepartmentConfig(
                name="reviewer",
                enable_caching=True,
                preferred_model="claude-sonnet-4-5",
            ),
            "product": DepartmentConfig(
                name="product",
                enable_caching=True,
            ),
            "ux": DepartmentConfig(
                name="ux",
                enable_caching=True,
            ),
            "qa": DepartmentConfig(
                name="qa",
                enable_caching=False,
            ),
            "delivery": DepartmentConfig(
                name="delivery",
                enable_caching=True,
            ),
            "devops": DepartmentConfig(
                name="devops",
                enable_caching=False,
                devops_build_fallback_commands=["python -m build"],
            ),
        },
        default_enable_caching=True,
        record_caching_stats=True,
        enable_coo_llm_routing=False,
    )


DEFAULT_COMPANY_CONFIG = default_company_config()


_COMPANY_AGENT_NAMES = (
    "coo",
    "product",
    "ux",
    "engineering",
    "reviewer",
    "qa",
    "delivery",
    "devops",
)


def _parse_bool_env(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled", "enable"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled", "disable", "none"}:
        return False
    return None


def apply_agent_caching_overrides_from_env(config: CompanyConfig | None = None) -> CompanyConfig:
    """Apply user-provided per-agent prompt caching overrides from environment variables.

    Supported forms:
    - AIDER_COMPANY_AGENT_CACHING="product:true,ux:false,engineering:true"
    - AIDER_COMPANY_CACHING_PRODUCT="true"
    - AIDER_COMPANY_CACHING_COO="false"
    """
    resolved = config or default_company_config()
    overrides: dict[str, bool] = {}

    packed = os.environ.get("AIDER_COMPANY_AGENT_CACHING", "")
    for chunk in packed.split(","):
        if not chunk.strip() or ":" not in chunk:
            continue
        name, value = chunk.split(":", 1)
        parsed = _parse_bool_env(value)
        if parsed is None:
            continue
        name = name.strip().lower()
        if name:
            overrides[name] = parsed

    for name in _COMPANY_AGENT_NAMES:
        value = os.environ.get(f"AIDER_COMPANY_CACHING_{name.upper()}")
        if value is None:
            continue
        parsed = _parse_bool_env(value)
        if parsed is not None:
            overrides[name] = parsed

    for name, enabled in overrides.items():
        dept_config = resolved.get_department_config(name)
        dept_config.enable_caching = enabled
        dept_config.cache_type = "auto" if enabled else "none"
        resolved.departments[name] = dept_config

    return resolved


def apply_devops_overrides_from_env(config: CompanyConfig | None = None) -> CompanyConfig:
    """Apply DevOps build/retry overrides from environment variables."""
    resolved = config or default_company_config()
    dept_config = resolved.get_department_config("devops")

    commands = os.environ.get("AIDER_DEVOPS_BUILD_FALLBACK_COMMANDS")
    if commands:
        dept_config.devops_build_fallback_commands = [
            chunk.strip() for chunk in commands.split(";;") if chunk.strip()
        ]

    attempts = os.environ.get("AIDER_DEVOPS_RETRY_ATTEMPTS")
    if attempts:
        try:
            dept_config.devops_retry_attempts = max(1, int(attempts))
        except ValueError:
            pass

    delay = os.environ.get("AIDER_DEVOPS_RETRY_BASE_DELAY")
    if delay:
        try:
            dept_config.devops_retry_base_delay = max(0.0, float(delay))
        except ValueError:
            pass

    log_dir = os.environ.get("AIDER_DEVOPS_LOG_CAPTURE_DIR")
    if log_dir:
        dept_config.devops_log_capture_dir = log_dir.strip() or dept_config.devops_log_capture_dir

    resolved.departments["devops"] = dept_config
    return resolved


def apply_agent_model_overrides_from_env(config: CompanyConfig | None = None) -> CompanyConfig:
    """Apply user-provided per-agent model overrides from environment variables.

    Supported forms:
    - AIDER_COMPANY_AGENT_MODELS="product=gpt-4o,engineering=claude-sonnet-4-5"
    - AIDER_COMPANY_MODEL_PRODUCT="gpt-4o"
    - AIDER_COMPANY_MODEL_COO="claude-sonnet-4-5"
    """
    resolved = config or default_company_config()
    overrides: dict[str, str] = {}

    packed = os.environ.get("AIDER_COMPANY_AGENT_MODELS", "")
    for chunk in packed.split(","):
        if "=" not in chunk:
            continue
        name, model = chunk.split("=", 1)
        name = name.strip().lower()
        model = model.strip()
        if name and model:
            overrides[name] = model

    for name in _COMPANY_AGENT_NAMES:
        model = os.environ.get(f"AIDER_COMPANY_MODEL_{name.upper()}")
        if model:
            overrides[name] = model.strip()

    for name, model in overrides.items():
        dept_config = resolved.get_department_config(name)
        dept_config.preferred_model = model
        resolved.departments[name] = dept_config

    return apply_devops_overrides_from_env(apply_agent_caching_overrides_from_env(resolved))
