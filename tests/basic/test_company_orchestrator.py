import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.product import ProductDepartment
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyEvent, Deliverable, EventMessage
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
                    "blocking": True,
                    "gate_name": "prd_approval",
                    "original_request": "Build a dashboard",
                },
            )

            seen_messages = []

            async def handler(message):
                seen_messages.append(message)

            orchestrator.on_deliverable(handler)
            route_task = asyncio.create_task(orchestrator._route(product_deliverable))
            await asyncio.sleep(0)

            engineering.receive.assert_not_awaited()
            approval_events = [
                message for message in seen_messages
                if isinstance(message, EventMessage)
                and message.event == CompanyEvent.APPROVAL_REQUIRED
            ]
            self.assertEqual(len(approval_events), 1)
            self.assertEqual(approval_events[0].payload["gate_name"], "prd_approval")
            self.assertEqual(approval_events[0].payload["approver_role"], "ceo")
            self.assertIn("Build a dashboard", approval_events[0].payload["artifact_preview"])

            orchestrator.approve("task-1")
            await route_task

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

            reply = await engineering.request_spec_clarification(
                "Which export formats are required?"
            )

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

    async def test_blocking_rejection_routes_prd_back_to_product_with_feedback(self):
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
                task_id="task-2",
                department="product",
                artifact_type="prd",
                payload="# PRD\n\nBuild a dashboard",
                status="success",
                metadata={
                    "handoff_to": "engineering",
                    "next_artifact_type": "prd",
                    "blocking": True,
                    "gate_name": "prd_approval",
                    "original_request": "Build a dashboard",
                },
            )

            route_task = asyncio.create_task(orchestrator._route(product_deliverable))
            await asyncio.sleep(0)
            orchestrator.request_changes(
                "task-2",
                "Add multi-tenant support before engineering starts",
            )
            await route_task

            revision_task = await product.inbox.get()

        engineering.receive.assert_not_awaited()
        self.assertEqual(revision_task.origin, "ceo")
        self.assertEqual(revision_task.target, "product")
        self.assertEqual(revision_task.payload["previous_prd"], product_deliverable.payload)
        self.assertEqual(
            revision_task.payload["ceo_feedback"],
            "Add multi-tenant support before engineering starts",
        )
        self.assertEqual(revision_task.payload["revision_count"], 1)


if __name__ == "__main__":
    unittest.main()
