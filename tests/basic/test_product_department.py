import tempfile
import unittest

from aider.company.departments.product import ProductDepartment
from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory


class TestProductDepartment(unittest.IsolatedAsyncioTestCase):
    async def test_process_generates_prd_handoff_with_blocking_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            department = ProductDepartment(project_memory=ProjectMemory(tmpdir))
            task = CompanyTask(
                task_id="task-1",
                origin="ceo",
                target="product",
                artifact_type="raw_prompt",
                payload="Build a dashboard",
            )

            deliverable = await department.process(task)

        self.assertEqual(deliverable.task_id, "task-1")
        self.assertEqual(deliverable.department, "product")
        self.assertEqual(deliverable.artifact_type, "prd")
        self.assertIn("# PRD", deliverable.payload)
        self.assertIn("Build a dashboard", deliverable.payload)
        self.assertEqual(deliverable.status, "success")
        self.assertEqual(deliverable.metadata["handoff_to"], "ux")
        self.assertTrue(deliverable.metadata["requires_design"])
        self.assertTrue(deliverable.metadata["blocking"])
        self.assertEqual(deliverable.metadata["gate_name"], "prd_approval")

    async def test_process_routes_non_design_prd_directly_to_engineering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            department = ProductDepartment(project_memory=ProjectMemory(tmpdir))
            task = CompanyTask(
                task_id="task-api-1",
                origin="ceo",
                target="product",
                artifact_type="raw_prompt",
                payload="Build an API endpoint",
            )

            deliverable = await department.process(task)

        self.assertEqual(deliverable.metadata["handoff_to"], "engineering")
        self.assertFalse(deliverable.metadata["requires_design"])

    async def test_process_revises_prd_with_ceo_feedback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            department = ProductDepartment(project_memory=ProjectMemory(tmpdir))
            task = CompanyTask(
                task_id="task-1",
                origin="ceo",
                target="product",
                artifact_type="prd",
                payload={
                    "previous_prd": "# PRD\n\nBuild a dashboard",
                    "ceo_feedback": "Add multi-tenant support before engineering starts",
                    "revision_count": 1,
                },
            )

            deliverable = await department.process(task)

        self.assertIn("# PRD", deliverable.payload)
        self.assertIn("CEO Feedback (Revision 1)", deliverable.payload)
        self.assertIn("Add multi-tenant support", deliverable.payload)
        self.assertTrue(deliverable.metadata["blocking"])
        self.assertEqual(deliverable.metadata["revision_count"], 1)


if __name__ == "__main__":
    unittest.main()
