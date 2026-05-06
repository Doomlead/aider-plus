from __future__ import annotations

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable


class QADepartment(Department):
    name = "qa"

    async def process(self, task: CompanyTask) -> Deliverable:
        engineering_output = (
            task.payload.get("engineering_result", {})
            if isinstance(task.payload, dict)
            else {}
        )
        engineering_metadata = (
            task.payload.get("engineering_metadata", {})
            if isinstance(task.payload, dict)
            else {}
        )
        if isinstance(engineering_output, dict):
            files_changed = engineering_output.get("metadata", {}).get("files", [])
        else:
            files_changed = engineering_metadata.get("files", [])

        prd_content = ""
        if isinstance(task.payload, dict):
            prd_content = task.payload.get("prd_content", "")
        test_plan = self._generate_test_plan(files_changed, prd_content)

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="test_report",
            payload=test_plan,
            status="success",
            metadata={
                "handoff_to": "ceo",
                "blocking": True,
                "gate_name": "release_approval",
                "test_coverage": "draft",
                "context": dict(task.context),
            },
        )

    @staticmethod
    def _generate_test_plan(files_changed, prd_content: str) -> dict:
        files = list(files_changed or [])
        focus_areas = [
            "Validate implementation against PRD requirements",
            "Exercise affected user flows and edge cases",
            "Run regression checks around changed modules",
        ]
        if files:
            focus_areas.append("Review changed files: " + ", ".join(files))

        return {
            "summary": "Draft QA test report for release approval.",
            "status": "ready_for_ceo_review",
            "files_changed": files,
            "prd_excerpt": str(prd_content)[:1000],
            "recommended_checks": [
                "Run the project test suite",
                "Run linting and formatting checks",
                "Run type checks where configured",
                "Perform targeted manual verification for the PRD acceptance criteria",
            ],
            "focus_areas": focus_areas,
        }
