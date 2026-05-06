import re

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable


class ProductDepartment(Department):
    name = "product"

    def get_context_requirements(self) -> list[str]:
        return ["playbook.*", "project.name", "project.phase"]

    async def process(self, task: CompanyTask) -> Deliverable:
        if task.origin == "engineering" or task.artifact_type == "memo":
            return self._process_engineering_clarification(task)

        # Lightweight PRD generation. Replace with real LLM call later.
        original_request = self._original_request(task.payload)
        prd = self._build_prd(task.payload, original_request)
        requires_design = self._requires_design(task.payload, original_request)
        handoff_to = "ux" if requires_design else "engineering"
        context = dict(task.context)
        if context:
            context["original_request"] = original_request
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="prd",
            payload=prd,
            status="success",
            metadata={
                "handoff_to": handoff_to,
                "next_artifact_type": "prd",
                "blocking": True,
                "gate_name": "prd_approval",
                "original_request": original_request,
                "revision_count": self._revision_count(task.payload),
                "requires_design": requires_design,
                "context": context,
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
            return (
                payload.get("original_request")
                or payload.get("prompt")
                or payload.get("previous_prd")
                or str(payload)
            )
        return str(payload)

    @classmethod
    def _build_prd(cls, payload, original_request: str) -> str:
        if not isinstance(payload, dict) or "previous_prd" not in payload:
            return f"# PRD\n\n## Vision\n{original_request}\n\n## Requirements\n- TBD\n"

        feedback = payload.get(
            "ceo_feedback", "Please revise before engineering starts"
        )
        revision_count = cls._revision_count(payload)
        return (
            f"{payload.get('previous_prd')}\n"
            f"\n## CEO Feedback (Revision {revision_count})\n{feedback}\n"
            "\n## Revision Notes\n- Address CEO feedback before engineering starts.\n"
        )

    @staticmethod
    def _revision_count(payload) -> int:
        if isinstance(payload, dict):
            return int(payload.get("revision_count", 0) or 0)
        return 0

    @staticmethod
    def _clarification_question(payload) -> str:
        if isinstance(payload, dict):
            return payload.get("question") or payload.get("description") or str(payload)
        return str(payload)

    @staticmethod
    def _requires_design(payload, original_request: str) -> bool:
        if isinstance(payload, dict) and "requires_design" in payload:
            return bool(payload.get("requires_design"))

        prompt_terms = set(re.findall(r"[a-z0-9]+", original_request.lower()))
        design_terms = {
            "ui",
            "ux",
            "design",
            "wireframe",
            "wireframes",
            "frontend",
            "screen",
            "screens",
            "dashboard",
            "component",
            "components",
            "layout",
            "css",
        }
        return (
            bool(prompt_terms & design_terms) or "front-end" in original_request.lower()
        )
