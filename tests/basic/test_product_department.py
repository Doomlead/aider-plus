import tempfile
import unittest

from aider.company.departments.product import ProductDepartment
from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory


class TestProductDepartment(unittest.IsolatedAsyncioTestCase):
    async def test_process_generates_prd_handoff_without_blocking_gate(self):
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
        self.assertEqual(deliverable.metadata["handoff_to"], "engineering")
        self.assertFalse(deliverable.metadata["blocking"])


if __name__ == "__main__":
    unittest.main()
