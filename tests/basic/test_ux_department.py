import tempfile
import unittest

from aider.company.departments.ux import UXDepartment
from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory


class TestUXDepartment(unittest.IsolatedAsyncioTestCase):
    async def test_process_generates_design_spec_handoff_to_engineering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            department = UXDepartment(project_memory=ProjectMemory(tmpdir))
            task = CompanyTask(
                task_id="task-ux-1",
                origin="product",
                target="ux",
                artifact_type="prd",
                payload={"prd_content": "# PRD\n\nBuild a dashboard"},
                context={
                    "project_name": "dashboard",
                    "prd_content": "# PRD\n\nBuild a dashboard",
                },
            )

            deliverable = await department.process(task)

        self.assertEqual(deliverable.task_id, "task-ux-1")
        self.assertEqual(deliverable.department, "ux")
        self.assertEqual(deliverable.artifact_type, "design_spec")
        self.assertEqual(deliverable.status, "success")
        self.assertEqual(deliverable.metadata["handoff_to"], "engineering")
        self.assertFalse(deliverable.metadata["blocking"])
        self.assertIn("acceptance_criteria", deliverable.payload)
        self.assertIn("css_variables", deliverable.payload)


if __name__ == "__main__":
    unittest.main()
