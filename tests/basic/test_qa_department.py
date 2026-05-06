import tempfile
import unittest
from pathlib import Path

from aider.company.departments.qa import QADepartment
from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory


class TestQADepartment(unittest.IsolatedAsyncioTestCase):
    async def test_generates_structured_release_test_report_without_test_files(self):
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
                        "engineering_metadata": {"files": ["app.py"]},
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
        self.assertTrue(deliverable.metadata["test_executed"])
        self.assertEqual(deliverable.payload["files_changed"], ["app.py"])
        self.assertEqual(deliverable.payload["files_covered"], ["app.py"])
        self.assertIsNone(deliverable.payload["test_passed"])
        self.assertIn("Manual verification required", deliverable.payload["test_results"])
        self.assertIn("Build a dashboard", deliverable.payload["prd_excerpt"])

    async def test_runs_pytest_for_changed_test_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "test_app.py"
            test_path.write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
            department = QADepartment(project_memory=ProjectMemory(tmpdir))
            deliverable = await department.process(
                CompanyTask(
                    task_id="qa-2",
                    origin="engineering",
                    target="qa",
                    artifact_type="code",
                    payload={
                        "engineering_result": "implemented",
                        "engineering_metadata": {"files": ["test_app.py"]},
                    },
                )
            )

        self.assertEqual(deliverable.status, "success")
        self.assertTrue(deliverable.payload["test_passed"])
        self.assertIn("pytest test_app.py -v --tb=short", deliverable.payload["test_results"])
        self.assertIn("1 passed", deliverable.payload["test_results"])


if __name__ == "__main__":
    unittest.main()
