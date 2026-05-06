import tempfile
import unittest

from aider.company.departments.qa import QADepartment
from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory


class TestQADepartment(unittest.IsolatedAsyncioTestCase):
    async def test_generates_structured_release_test_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            department = QADepartment(project_memory=ProjectMemory(tmpdir))
            deliverable = await department.process(
                CompanyTask(
                    task_id="qa-1",
                    origin="engineering",
                    target="qa",
                    artifact_type="code",
                    payload={
                        "engineering_result": "implemented",
                        "engineering_metadata": {
                            "files": ["app.py", "tests/test_app.py"]
                        },
                        "prd_content": "Build a dashboard",
                    },
                    context={"project_name": "dashboard"},
                )
            )

        self.assertEqual(deliverable.task_id, "qa-1")
        self.assertEqual(deliverable.department, "qa")
        self.assertEqual(deliverable.artifact_type, "test_report")
        self.assertEqual(deliverable.status, "success")
        self.assertEqual(deliverable.metadata["gate_name"], "release_approval")
        self.assertTrue(deliverable.metadata["blocking"])
        self.assertEqual(deliverable.metadata["handoff_to"], "ceo")
        self.assertEqual(
            deliverable.payload["files_changed"], ["app.py", "tests/test_app.py"]
        )
        self.assertIn("Build a dashboard", deliverable.payload["prd_excerpt"])


if __name__ == "__main__":
    unittest.main()
