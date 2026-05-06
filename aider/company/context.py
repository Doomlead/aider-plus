from __future__ import annotations

from typing import Iterable, Optional

from aider.company.project import Project
from aider.company.schemas import CompanyTask
from aider.company.state import CompanyStateManager


class ContextBuilder:
    """Build department task context from declared requirements and project state."""

    def __init__(self, state: CompanyStateManager):
        self.state = state

    def build(
        self,
        task: CompanyTask,
        requirements: Iterable[str],
        project: Optional[Project] = None,
    ) -> dict:
        context = dict(task.context or {})
        requirements = list(requirements or [])

        if project is not None:
            if "project.name" in requirements:
                context.setdefault("project_name", project.name)
            if "project.phase" in requirements:
                context.setdefault("project_phase", project.phase)
            if "project.prd" in requirements and project.prd:
                context.setdefault("prd_content", project.prd)
            if "project.design_spec" in requirements and project.design_spec:
                context.setdefault("design_spec", project.design_spec)

        playbook = self._requested_playbook(requirements)
        if playbook:
            context["playbook"] = playbook
            context["playbook_guidance"] = self._format_playbook_guidance(playbook)

        return context

    def _requested_playbook(self, requirements: list[str]) -> dict:
        if "playbook.*" in requirements:
            return {
                key: list(value or [])
                for key, value in self.state.get_playbook().items()
                if isinstance(value, list) and value
            }

        requested = {}
        playbook = self.state.get_playbook()
        for requirement in requirements:
            if not requirement.startswith("playbook."):
                continue
            key = requirement.split(".", 1)[1]
            values = playbook.get(key)
            if isinstance(values, list) and values:
                requested[key] = list(values)
        return requested

    @staticmethod
    def _format_playbook_guidance(playbook: dict) -> list[str]:
        guidance = []
        for entries in playbook.values():
            for entry in entries:
                guidance.append(str(entry))
        return guidance
