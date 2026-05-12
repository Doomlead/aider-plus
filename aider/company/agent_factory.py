from __future__ import annotations

from dataclasses import replace
from typing import Awaitable, Callable, Optional

from aider import models
from aider.agent import AiderAgentLoop
from aider.agent.loop import AgentLoopConfig
from aider.company.config import CompanyConfig, default_company_config

AgentCallback = Optional[Callable[[str, dict], Awaitable[None]]]


DEPARTMENT_AGENT_NAMES = ("coo", "product", "ux", "engineering", "qa", "devops")
COMPANY_AGENT_ROLE_NAMES = DEPARTMENT_AGENT_NAMES


def clone_coder_for_agent(coder, *, model_name: str | None = None):
    """Return an isolated coder for one company agent when the coder supports cloning."""
    clone = getattr(coder, "clone", None)
    if not callable(clone):
        return coder

    kwargs = {}
    if model_name:
        kwargs["main_model"] = models.Model(model_name)
    return clone(**kwargs)


def agent_loop_config_for_department(
    base_config: AgentLoopConfig | None,
    *,
    model_name: str | None = None,
) -> AgentLoopConfig:
    """Copy the base agent-loop config and pin department models when requested."""
    config = replace(base_config) if base_config is not None else AgentLoopConfig()
    if model_name:
        if config.architect_model is None:
            config.architect_model = model_name
        if config.editor_model is None:
            config.editor_model = model_name
    return config


def build_agent_loop_for_department(
    *,
    coder,
    department_name: str,
    company_config: CompanyConfig | None = None,
    callback: AgentCallback = None,
    base_config: AgentLoopConfig | None = None,
) -> AiderAgentLoop:
    """Build a dedicated AiderAgentLoop for one COO or department agent."""
    resolved_company_config = company_config or default_company_config()
    dept_config = resolved_company_config.get_department_config(department_name)
    department_coder = clone_coder_for_agent(
        coder, model_name=dept_config.preferred_model
    )
    loop_config = agent_loop_config_for_department(
        base_config,
        model_name=dept_config.preferred_model,
    )
    return AiderAgentLoop(
        coder=department_coder,
        callback=callback,
        config=loop_config,
        enable_prompt_caching=dept_config.enable_prompt_caching,
    )


def build_agent_loop_for_role(
    *,
    coder,
    role_name: str,
    company_config: CompanyConfig | None = None,
    callback: AgentCallback = None,
    base_config: AgentLoopConfig | None = None,
) -> AiderAgentLoop:
    """Build a dedicated AiderAgentLoop for any company role, including COO."""
    return build_agent_loop_for_department(
        coder=coder,
        department_name=role_name,
        company_config=company_config,
        callback=callback,
        base_config=base_config,
    )


def build_company_agent_loops(
    *,
    coder,
    company_config: CompanyConfig | None = None,
    callback: AgentCallback = None,
    base_config: AgentLoopConfig | None = None,
) -> dict[str, AiderAgentLoop]:
    """Build one dedicated AiderAgentLoop per company agent role."""
    return {
        name: build_agent_loop_for_department(
            coder=coder,
            department_name=name,
            company_config=company_config,
            callback=callback,
            base_config=base_config,
        )
        for name in DEPARTMENT_AGENT_NAMES
    }
