from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable


class ProductDepartment(Department):
    name = "product"

    async def process(self, task: CompanyTask) -> Deliverable:
        # Lightweight PRD generation. Replace with real LLM call later.
        prd = f"# PRD\n\n## Vision\n{task.payload}\n\n## Requirements\n- TBD\n"
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="prd",
            payload=prd,
            status="success",
            metadata={"handoff_to": "engineering", "blocking": False},
        )
