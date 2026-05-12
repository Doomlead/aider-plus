"""Factories for department-owned Company Mode agents."""

from __future__ import annotations

from dataclasses import replace
from typing import Awaitable, Callable, Optional

from aider import models
from aider.agent import AiderAgentLoop
from aider.agent.loop import AgentLoopConfig
from aider.company.config import CompanyConfig, DepartmentConfig

AgentEventCallback = Callable[[str, dict], Awaitable[None]]


class DepartmentAgentFactory:
    """Build isolated AiderAgentLoop instances for each department.

    The factory keeps the public integration code from accidentally sharing one
    mutable agent loop across Product, UX, Engineering, and future LLM-backed
    departments. Each created loop has its own ToolRegistry, prompt-cache flag,
    DepartmentConfig, and optional model override.
    """

    def __init__(
        self,
        *,
        coder,
        company_config: CompanyConfig,
        base_config: Optional[AgentLoopConfig] = None,
        callback: Optional[AgentEventCallback] = None,
    ):
        self.coder = coder
        self.company_config = company_config
        self.base_config = base_config or AgentLoopConfig()
        self.callback = callback

    def create(self, department_name: str) -> AiderAgentLoop:
        dept_config = self.company_config.get_department_config(department_name)
        loop_config = self._loop_config_for(dept_config)
        loop_coder = self._coder_for(dept_config)
        loop = AiderAgentLoop(
            coder=loop_coder,
            callback=self._callback_for(department_name),
            config=loop_config,
            enable_prompt_caching=dept_config.enable_prompt_caching,
        )
        loop.config.department_config = dept_config
        reviewer_config = self.company_config.departments.get("reviewer")
        if reviewer_config is not None:
            loop.reviewer_department_config = reviewer_config
            if getattr(loop.config, "reviewer_model", None) is None:
                loop.config.reviewer_model = reviewer_config.preferred_model
        return loop

    def _loop_config_for(self, dept_config: DepartmentConfig) -> AgentLoopConfig:
        loop_config = replace(self.base_config)
        if dept_config.preferred_model:
            loop_config.architect_model = dept_config.preferred_model
            if not loop_config.editor_model:
                loop_config.editor_model = dept_config.preferred_model
        return loop_config

    def _coder_for(self, dept_config: DepartmentConfig):
        if not hasattr(self.coder, "clone"):
            return self.coder
        if dept_config.preferred_model:
            return self.coder.clone(main_model=models.Model(dept_config.preferred_model))
        return self.coder.clone()

    def _callback_for(self, department_name: str) -> Optional[AgentEventCallback]:
        if self.callback is None:
            return None

        async def department_callback(event_name: str, payload: dict) -> None:
            await self.callback(
                event_name,
                {"department": department_name, **dict(payload or {})},
            )

        return department_callback
