from __future__ import annotations

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable


class UXDepartment(Department):
    name = "ux"
    allowed_tools = ["design_spec"]

    async def process(self, task: CompanyTask) -> Deliverable:
        prd_content = self._prd_content(task)
        design_spec = {
            "summary": "UX acceptance criteria and implementation guidance.",
            "acceptance_criteria": self._acceptance_criteria(prd_content),
            "wireframes": [
                "Map the primary user journey from entry point to successful completion.",
                "Show empty, loading, success, and error states for each primary screen.",
            ],
            "component_choices": [
                "Use existing design-system components before introducing new UI primitives.",
                "Prefer accessible form controls, buttons, tables, and status messaging.",
            ],
            "css_variables": {
                "--color-primary": "var(--aider-color-primary, #2563eb)",
                "--color-surface": "var(--aider-color-surface, #ffffff)",
                "--space-unit": "var(--aider-space-unit, 0.25rem)",
            },
            "prd_excerpt": str(prd_content)[:1000],
        }
        context = dict(task.context)
        context["design_spec"] = design_spec
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="design_spec",
            payload=design_spec,
            status="success",
            metadata={
                "handoff_to": "engineering",
                "next_artifact_type": "prd",
                "blocking": False,
                "context": context,
            },
        )

    @staticmethod
    def _prd_content(task: CompanyTask) -> str:
        if isinstance(task.payload, dict):
            return str(
                task.payload.get("prd_content")
                or task.payload.get("prd")
                or task.payload
            )
        return str(task.payload)

    @staticmethod
    def _acceptance_criteria(prd_content: str) -> list[str]:
        return [
            "The implementation satisfies the Product PRD without regressing existing behavior.",
            "The primary workflow is discoverable and can be completed without hidden steps.",
            "Interactive states provide clear feedback for loading, success, validation, and errors.",
            "The UI remains usable with keyboard navigation and assistive technologies.",
        ]
