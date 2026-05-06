from __future__ import annotations

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable


class DevOpsDepartment(Department):
    name = "devops"
    allowed_tools = ["shell", "docker", "git_tag", "deploy"]

    async def process(self, task: CompanyTask) -> Deliverable:
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="general",
            payload="DevOps task received. Infrastructure execution is not implemented yet.",
            status="needs_review",
            metadata={"allowed_tools": self.allowed_tools, "context": dict(task.context)},
        )
