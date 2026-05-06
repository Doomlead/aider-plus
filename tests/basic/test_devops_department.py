import tempfile
import unittest

from aider.company.departments.devops import DevOpsDepartment
from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory


class TestDevOpsDepartment(unittest.IsolatedAsyncioTestCase):
    async def test_process_generates_successful_deploy_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            department = DevOpsDepartment(project_memory=ProjectMemory(tmpdir))
            task = CompanyTask(
                task_id="task-deploy-1",
                origin="ceo",
                target="devops",
                artifact_type="deploy_request",
                payload={"engineering_result": "implemented", "qa_report": "QA passed"},
                context={"project_name": "dashboard"},
            )

            deliverable = await department.process(task)

        self.assertEqual(deliverable.task_id, "task-deploy-1")
        self.assertEqual(deliverable.department, "devops")
        self.assertEqual(deliverable.artifact_type, "deploy_report")
        self.assertEqual(deliverable.status, "success")
        self.assertEqual(deliverable.metadata["handoff_to"], "ceo")
        self.assertFalse(deliverable.metadata["blocking"])
        self.assertEqual(deliverable.metadata["git_tag"], "v1.0.0")
        self.assertEqual(
            deliverable.metadata["deploy_url"], "https://dashboard.example.com"
        )


if __name__ == "__main__":
    unittest.main()
