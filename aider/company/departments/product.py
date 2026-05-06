from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable


class ProductDepartment(Department):
    name = "product"

    async def process(self, task: CompanyTask) -> Deliverable:
        if task.origin == "engineering" or task.artifact_type == "memo":
            return self._process_engineering_clarification(task)

        # Lightweight PRD generation. Replace with real LLM call later.
        original_request = self._original_request(task.payload)
        prd = f"# PRD\n\n## Vision\n{original_request}\n\n## Requirements\n- TBD\n"
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="prd",
            payload=prd,
            status="success",
            metadata={
                "handoff_to": "engineering",
                "next_artifact_type": "prd",
                "blocking": False,
                "original_request": original_request,
            },
        )

    def _process_engineering_clarification(self, task: CompanyTask) -> Deliverable:
        question = self._clarification_question(task.payload)
        response = f"Product clarification: {question}"
        context = dict(task.context)
        context["last_clarification_question"] = question
        context["last_clarification_response"] = response
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="memo",
            payload=response,
            status="success",
            metadata={
                "handoff_to": "engineering",
                "next_artifact_type": "memo",
                "blocking": False,
                "context": context,
            },
        )

    @staticmethod
    def _original_request(payload) -> str:
        if isinstance(payload, dict):
            return payload.get("original_request") or payload.get("prompt") or str(payload)
        return str(payload)

    @staticmethod
    def _clarification_question(payload) -> str:
        if isinstance(payload, dict):
            return payload.get("question") or payload.get("description") or str(payload)
        return str(payload)
