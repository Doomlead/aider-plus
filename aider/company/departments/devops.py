from __future__ import annotations

from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.department import Department
from aider.memory import ConversationMemory, ProjectMemory
from aider.company.schemas import CompanyTask, Deliverable


class DevOpsDepartment(Department):
    name = "devops"
    allowed_tools = ["shell", "docker_build", "deploy", "git_tag"]

    def __init__(
        self,
        project_memory: ProjectMemory,
        agent_loop: Optional[AiderAgentLoop] = None,
        conversation_memory: Optional[ConversationMemory] = None,
        config: Optional[DepartmentConfig] = None,
    ):
        super().__init__(project_memory, conversation_memory, config=config)
        self.agent_loop = agent_loop

    def get_context_requirements(self) -> list[str]:
        return ["playbook.deployment_gotchas", "project.name", "project.phase"]

    async def process(self, task: CompanyTask) -> Deliverable:
        release_artifact = self._release_artifact(task)
        playbook_guidance = task.context.get("playbook_guidance", [])
        git_tag = self._git_tag(task)
        deploy_url = self._deploy_url(task)
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="deploy_report",
            payload={
                "summary": "Deployment completed successfully.",
                "release_artifact": release_artifact,
                "deploy_url": deploy_url,
                "git_tag": git_tag,
                "environment": self._environment(task),
                "playbook_guidance": playbook_guidance,
            },
            status="success",
            metadata={
                "deploy_url": deploy_url,
                "git_tag": git_tag,
                "handoff_to": "ceo",
                "blocking": False,
                "context": dict(task.context),
            },
        )

    @staticmethod
    def _release_artifact(task: CompanyTask):
        if isinstance(task.payload, dict):
            return task.payload.get("engineering_result") or task.payload.get(
                "release_artifact"
            )
        return task.payload

    @staticmethod
    def _environment(task: CompanyTask) -> str:
        if isinstance(task.payload, dict):
            return str(task.payload.get("environment") or "production")
        return "production"

    @classmethod
    def _deploy_url(cls, task: CompanyTask) -> str:
        if isinstance(task.payload, dict) and task.payload.get("deploy_url"):
            return str(task.payload["deploy_url"])
        project_name = str(task.context.get("project_name") or "app").strip().lower()
        safe_name = "-".join(
            part for part in project_name.replace("_", "-").split() if part
        )
        return f"https://{safe_name or 'app'}.example.com"

    @staticmethod
    def _git_tag(task: CompanyTask) -> str:
        if isinstance(task.payload, dict) and task.payload.get("git_tag"):
            return str(task.payload["git_tag"])
        return "v1.0.0"
