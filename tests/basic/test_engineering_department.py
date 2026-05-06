import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aider.company.departments.engineering import EngineeringDepartment
from aider.company.schemas import CompanyTask
from aider.memory import ConversationMemory, ProjectMemory


class TestEngineeringDepartment(unittest.IsolatedAsyncioTestCase):
    async def test_process_runs_agent_loop_with_task_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_loop = SimpleNamespace(
                coder=SimpleNamespace(conversation_memory=ConversationMemory()),
                run=AsyncMock(
                    return_value={
                        "summary": "implemented",
                        "coder_result": {
                            "summary": "edited files",
                            "files_changed": ["app.py"],
                            "commit_hash": "abc123",
                            "diff": "diff --git a/app.py b/app.py",
                        },
                    }
                ),
            )
            department_memory = ConversationMemory()
            department = EngineeringDepartment(
                project_memory=ProjectMemory(tmpdir),
                agent_loop=agent_loop,
                conversation_memory=department_memory,
            )
            task = CompanyTask(
                task_id="task-1",
                origin="ceo",
                target="engineering",
                artifact_type="raw_prompt",
                payload="Fix the bug",
            )

            deliverable = await department.process(task)

        agent_loop.run.assert_awaited_once_with("Fix the bug")
        self.assertEqual(deliverable.payload, "implemented")
        self.assertEqual(deliverable.status, "success")
        self.assertEqual(deliverable.metadata["files"], ["app.py"])
        self.assertEqual(deliverable.metadata["commits"], ["abc123"])
        self.assertEqual(deliverable.metadata["diffs"], ["diff --git a/app.py b/app.py"])
        self.assertEqual(
            department_memory.get(),
            [
                {"role": "user", "content": "Fix the bug"},
                {"role": "assistant", "content": "implemented"},
            ],
        )

    async def test_process_does_not_duplicate_agent_loop_conversation_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shared_memory = ConversationMemory()
            agent_loop = SimpleNamespace(
                coder=SimpleNamespace(conversation_memory=shared_memory),
                run=AsyncMock(return_value={"summary": "done"}),
            )
            department = EngineeringDepartment(
                project_memory=ProjectMemory(tmpdir),
                agent_loop=agent_loop,
                conversation_memory=shared_memory,
            )
            task = CompanyTask(
                task_id="task-2",
                origin="ceo",
                target="engineering",
                artifact_type="raw_prompt",
                payload="Ship it",
            )

            await department.process(task)

        agent_loop.run.assert_awaited_once_with("Ship it")
        self.assertEqual(shared_memory.get(), [])


if __name__ == "__main__":
    unittest.main()
