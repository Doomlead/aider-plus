import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.product import ProductDepartment
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import Deliverable
from aider.memory import ConversationMemory, ProjectMemory


class TestCompanyOrchestrator(unittest.IsolatedAsyncioTestCase):
    async def test_product_prd_handoff_injects_prd_content_for_engineering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            product = ProductDepartment(project_memory=memory)
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(run=AsyncMock(return_value={"summary": "done"})),
            )
            engineering.receive = AsyncMock()
            orchestrator.register(product)
            orchestrator.register(engineering)

            product_deliverable = Deliverable(
                task_id="task-1",
                department="product",
                artifact_type="prd",
                payload="# PRD\n\nBuild a dashboard",
                status="success",
                metadata={
                    "handoff_to": "engineering",
                    "next_artifact_type": "prd",
                    "blocking": False,
                    "original_request": "Build a dashboard",
                },
            )

            await orchestrator._route(product_deliverable)

        engineering.receive.assert_awaited_once()
        engineering_task = engineering.receive.await_args.args[0]
        self.assertEqual(engineering_task.origin, "product")
        self.assertEqual(engineering_task.target, "engineering")
        self.assertEqual(engineering_task.artifact_type, "prd")
        self.assertIsNotNone(engineering_task.payload.get("prd_content"))
        self.assertEqual(engineering_task.payload["prd_content"], product_deliverable.content)
        self.assertEqual(engineering_task.payload["original_request"], "Build a dashboard")
        self.assertEqual(engineering_task.context["prd_content"], product_deliverable.content)

    async def test_engineering_clarification_routes_product_response_back_to_engineering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            product = ProductDepartment(project_memory=memory)
            agent_loop = SimpleNamespace(
                run=AsyncMock(return_value={"summary": "implemented with clarification"})
            )
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=agent_loop,
                conversation_memory=ConversationMemory(),
            )
            orchestrator.register(product)
            orchestrator.register(engineering)

            reply = await engineering.request_spec_clarification("Which export formats are required?")

            product_task = await product.inbox.get()
            product_deliverable = await product.process(product_task)
            await orchestrator._route(product_deliverable)
            engineering_task = await engineering.inbox.get()
            engineering_deliverable = await engineering.process(engineering_task)

        self.assertEqual(
            reply,
            "Clarification request sent to Product: Which export formats are required?",
        )
        self.assertEqual(product_task.origin, "engineering")
        self.assertEqual(product_task.target, "product")
        self.assertEqual(product_task.payload["question"], "Which export formats are required?")
        self.assertEqual(engineering_task.origin, "product")
        self.assertEqual(engineering_task.target, "engineering")
        self.assertEqual(
            engineering_task.payload["last_clarification_question"],
            "Which export formats are required?",
        )
        self.assertIn("Product clarification", engineering_task.payload["clarification_response"])
        agent_loop.run.assert_awaited_once()
        self.assertIn("Which export formats are required?", agent_loop.run.await_args.args[0])
        self.assertEqual(engineering_deliverable.status, "success")


if __name__ == "__main__":
    unittest.main()
