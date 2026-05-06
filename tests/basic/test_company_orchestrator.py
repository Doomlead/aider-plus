import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aider.company.department import Department
from aider.company.departments.devops import DevOpsDepartment
from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.product import ProductDepartment
from aider.company.departments.qa import QADepartment
from aider.company.departments.ux import UXDepartment
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.project import Project
from aider.company.schemas import CompanyEvent, CompanyTask, Deliverable, EventMessage
from aider.memory import ConversationMemory, ProjectMemory


class FakeQADepartment(Department):
    name = "qa"

    async def process(self, task):
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="test_report",
            payload="QA passed",
            status="success",
            metadata={"context": dict(task.context)},
        )


class TestCompanyOrchestrator(unittest.IsolatedAsyncioTestCase):

    async def test_handle_approval_response_deduplicates_duplicate_ui_clicks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
            )
            devops = DevOpsDepartment(project_memory=memory)
            engineering.receive = AsyncMock()
            devops.receive = AsyncMock()
            orchestrator.register(engineering)
            orchestrator.register(devops)
            task = CompanyTask(
                task_id="duplicate-approval-1",
                origin="product",
                target="engineering",
                artifact_type="prd",
                payload="Build the feature",
                blocking=True,
            )

            submit_task = asyncio.create_task(orchestrator.submit(task))
            await asyncio.sleep(0)

            first = await orchestrator.handle_approval_response(
                "duplicate-approval-1", True, source="discord"
            )
            second = await orchestrator.handle_approval_response(
                "duplicate-approval-1", True, source="discord"
            )
            await submit_task

            self.assertTrue(first)
            self.assertFalse(second)
            engineering.receive.assert_awaited_once()
            self.assertIn("duplicate-approval-1", orchestrator._resolved_task_ids)

    async def test_product_prd_handoff_injects_prd_content_for_engineering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            product = ProductDepartment(project_memory=memory)
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
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
                message
                for message in seen_messages
                if isinstance(message, EventMessage)
                and message.event == CompanyEvent.APPROVAL_REQUIRED
            ]
            self.assertEqual(len(approval_events), 1)
            self.assertEqual(approval_events[0].payload["gate_name"], "prd_approval")
            self.assertEqual(approval_events[0].payload["approver_role"], "ceo")
            self.assertIn(
                "Build a dashboard", approval_events[0].payload["artifact_preview"]
            )

            orchestrator.approve("task-1")
            await route_task

        engineering.receive.assert_awaited_once()
        engineering_task = engineering.receive.await_args.args[0]
        self.assertEqual(engineering_task.origin, "product")
        self.assertEqual(engineering_task.target, "engineering")
        self.assertEqual(engineering_task.artifact_type, "prd")
        self.assertIsNotNone(engineering_task.payload.get("prd_content"))
        self.assertEqual(
            engineering_task.payload["prd_content"], product_deliverable.content
        )
        self.assertEqual(
            engineering_task.payload["original_request"], "Build a dashboard"
        )
        self.assertEqual(
            engineering_task.context["prd_content"], product_deliverable.content
        )

    async def test_design_required_routes_product_to_ux_before_engineering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            orchestrator.active_project = Project(
                project_id="project-design-1",
                name="dashboard",
                phase="prototyping",
            )
            ux = UXDepartment(project_memory=memory)
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
            )
            ux.receive = AsyncMock()
            engineering.receive = AsyncMock()
            orchestrator.register(ux)
            orchestrator.register(engineering)

            product_deliverable = Deliverable(
                task_id="task-design-1",
                department="product",
                artifact_type="prd",
                payload="# PRD\n\nBuild a dashboard",
                status="success",
                metadata={
                    "handoff_to": "ux",
                    "next_artifact_type": "prd",
                    "blocking": True,
                    "gate_name": "prd_approval",
                    "original_request": "Build a dashboard",
                    "requires_design": True,
                    "context": {"project_name": "dashboard"},
                },
            )

            route_task = asyncio.create_task(orchestrator._route(product_deliverable))
            await asyncio.sleep(0)
            orchestrator.approve("task-design-1")
            await route_task

            self.assertEqual(orchestrator.active_project.phase, "design")
            ux.receive.assert_awaited_once()
            engineering.receive.assert_not_awaited()
            ux_task = ux.receive.await_args.args[0]
            self.assertEqual(ux_task.target, "ux")
            self.assertEqual(
                ux_task.payload["prd_content"], product_deliverable.payload
            )

            ux_deliverable = Deliverable(
                task_id="task-design-1",
                department="ux",
                artifact_type="design_spec",
                payload={"acceptance_criteria": ["Dashboard is usable"]},
                status="success",
                metadata={"context": dict(ux_task.context)},
            )
            await orchestrator._route(ux_deliverable)

            self.assertEqual(orchestrator.active_project.phase, "development")
            self.assertEqual(
                orchestrator.active_project.design_spec,
                {"acceptance_criteria": ["Dashboard is usable"]},
            )
            engineering.receive.assert_awaited_once()
            engineering_task = engineering.receive.await_args.args[0]
            self.assertEqual(engineering_task.target, "engineering")
            self.assertEqual(
                engineering_task.payload["prd_content"], product_deliverable.payload
            )
            self.assertEqual(
                engineering_task.payload["design_spec"],
                {"acceptance_criteria": ["Dashboard is usable"]},
            )

    async def test_blocking_approval_persists_and_clears_pending_approval(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
            )
            engineering.receive = AsyncMock()
            orchestrator.register(engineering)

            task = CompanyTask(
                task_id="persist-approval-1",
                origin="product",
                target="engineering",
                artifact_type="prd",
                payload={"prd_content": "Build a subscription billing dashboard"},
                blocking=True,
                context={"project_name": "billing", "gate_name": "prd_approval"},
            )

            submit_task = asyncio.create_task(orchestrator.submit(task))
            await asyncio.sleep(0)

            self.assertEqual(len(memory.data["pending_approvals"]), 1)
            pending = memory.data["pending_approvals"][0]
            self.assertEqual(pending["task_id"], "persist-approval-1")
            self.assertEqual(pending["gate_name"], "prd_approval")
            self.assertEqual(pending["department"], "product")
            self.assertEqual(pending["status"], "pending")
            self.assertIn("subscription billing dashboard", pending["artifact_preview"])

            loaded = ProjectMemory(tmpdir)
            loaded.load()
            self.assertEqual(
                loaded.data["pending_approvals"][0]["task_id"], "persist-approval-1"
            )

            orchestrator.approve("persist-approval-1")
            await submit_task

            self.assertEqual(memory.data["pending_approvals"], [])
            engineering.receive.assert_awaited_once()

    async def test_recover_pending_approval_recreates_gate_and_reroutes_after_approval(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            memory.update(
                {
                    "pending_approvals": [
                        {
                            "task_id": "recover-approval-1",
                            "gate_name": "prd_approval",
                            "department": "product",
                            "artifact_preview": "Build a subscription billing dashboard",
                            "timestamp": "2024-01-15T10:00:00Z",
                            "status": "pending",
                            "task": {
                                "task_id": "recover-approval-1",
                                "origin": "product",
                                "target": "engineering",
                                "artifact_type": "prd",
                                "payload": {
                                    "prd_content": "# PRD\n\nBuild a subscription billing dashboard",
                                    "original_request": "Build a dashboard",
                                },
                                "blocking": True,
                                "context": {
                                    "project_name": "billing",
                                    "prd_metadata": {"gate_name": "prd_approval"},
                                },
                            },
                        }
                    ]
                }
            )
            memory.persist()

            recovered_memory = ProjectMemory(tmpdir)
            recovered_memory.load()
            orchestrator = CompanyOrchestrator(project_memory=recovered_memory)
            engineering = EngineeringDepartment(
                project_memory=recovered_memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
            )
            devops = DevOpsDepartment(project_memory=memory)
            engineering.receive = AsyncMock()
            devops.receive = AsyncMock()
            orchestrator.register(engineering)
            orchestrator.register(devops)
            seen_messages = []

            async def handler(message):
                seen_messages.append(message)

            orchestrator.on_deliverable(handler)

            await orchestrator.recover_pending_approvals()

            self.assertIn("recover-approval-1", orchestrator._gates)
            approval_events = [
                message
                for message in seen_messages
                if isinstance(message, EventMessage)
                and message.event == CompanyEvent.APPROVAL_REQUIRED
            ]
            self.assertEqual(len(approval_events), 1)
            self.assertEqual(approval_events[0].task_id, "recover-approval-1")
            self.assertEqual(approval_events[0].payload["gate_name"], "prd_approval")

            orchestrator.approve("recover-approval-1")
            await orchestrator._recovered_gate_tasks["recover-approval-1"]

            engineering.receive.assert_awaited_once()
            routed_task = engineering.receive.await_args.args[0]
            self.assertEqual(routed_task.task_id, "recover-approval-1")
            self.assertFalse(routed_task.blocking)
            self.assertEqual(recovered_memory.data["pending_approvals"], [])

    async def test_engineering_clarification_routes_product_response_back_to_engineering(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            product = ProductDepartment(project_memory=memory)
            agent_loop = SimpleNamespace(
                run=AsyncMock(
                    return_value={"summary": "implemented with clarification"}
                )
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
        self.assertEqual(
            product_task.payload["question"], "Which export formats are required?"
        )
        self.assertEqual(engineering_task.origin, "product")
        self.assertEqual(engineering_task.target, "engineering")
        self.assertEqual(
            engineering_task.payload["last_clarification_question"],
            "Which export formats are required?",
        )
        self.assertIn(
            "Product clarification", engineering_task.payload["clarification_response"]
        )
        agent_loop.run.assert_awaited_once()
        self.assertIn(
            "Which export formats are required?", agent_loop.run.await_args.args[0]
        )
        self.assertEqual(engineering_deliverable.status, "success")

    async def test_blocking_rejection_routes_prd_back_to_product_with_feedback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            product = ProductDepartment(project_memory=memory)
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
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
        self.assertEqual(
            revision_task.payload["previous_prd"], product_deliverable.payload
        )
        self.assertEqual(
            revision_task.payload["ceo_feedback"],
            "Add multi-tenant support before engineering starts",
        )
        self.assertEqual(revision_task.payload["revision_count"], 1)

    async def test_project_state_advances_from_prd_approval_to_qa(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            orchestrator.active_project = Project(
                project_id="project-1",
                name="dashboard",
                phase="prototyping",
            )
            product = ProductDepartment(project_memory=memory)
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
            )
            qa = QADepartment(project_memory=memory)
            engineering.receive = AsyncMock()
            qa.receive = AsyncMock()
            orchestrator.register(product)
            orchestrator.register(engineering)
            orchestrator.register(qa)

            product_deliverable = Deliverable(
                task_id="task-state-1",
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
                    "context": {"project_name": "dashboard"},
                },
            )

            route_task = asyncio.create_task(orchestrator._route(product_deliverable))
            await asyncio.sleep(0)
            self.assertEqual(orchestrator.active_project.phase, "prototyping")

            orchestrator.approve("task-state-1")
            await route_task

            self.assertEqual(orchestrator.active_project.phase, "development")
            self.assertEqual(
                orchestrator.active_project.prd, product_deliverable.payload
            )
            engineering.receive.assert_awaited_once()

            engineering_deliverable = Deliverable(
                task_id="task-state-1",
                department="engineering",
                artifact_type="code",
                payload="implemented",
                status="success",
                metadata={"context": {"project_name": "dashboard"}},
            )
            await orchestrator._route(engineering_deliverable)

            self.assertEqual(orchestrator.active_project.phase, "qa")
            self.assertEqual(
                orchestrator.active_project.engineering_result, engineering_deliverable
            )
            qa.receive.assert_awaited_once()
            qa_task = qa.receive.await_args.args[0]
            self.assertEqual(qa_task.origin, "engineering")
            self.assertEqual(qa_task.target, "qa")
            self.assertEqual(qa_task.payload["engineering_result"], "implemented")

    async def test_project_state_release_gate_approval_and_rejection_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            orchestrator.active_project = Project(
                project_id="project-2",
                name="dashboard",
                phase="qa",
            )
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
            )
            devops = DevOpsDepartment(project_memory=memory)
            engineering.receive = AsyncMock()
            devops.receive = AsyncMock()
            orchestrator.register(engineering)
            orchestrator.register(devops)
            seen_messages = []

            async def handler(message):
                seen_messages.append(message)

            orchestrator.on_deliverable(handler)
            qa_deliverable = Deliverable(
                task_id="task-release-1",
                department="qa",
                artifact_type="test_report",
                payload="QA passed",
                status="success",
                metadata={"context": {"project_name": "dashboard"}},
            )

            route_task = asyncio.create_task(orchestrator._route(qa_deliverable))
            await asyncio.sleep(0)

            self.assertEqual(orchestrator.active_project.phase, "release_ready")
            release_events = [
                message
                for message in seen_messages
                if isinstance(message, EventMessage)
                and message.event == CompanyEvent.APPROVAL_REQUIRED
            ]
            self.assertEqual(len(release_events), 1)
            self.assertEqual(release_events[0].payload["gate_name"], "release_approval")
            self.assertIn("QA passed", release_events[0].payload["artifact_preview"])

            orchestrator.approve(release_events[0].task_id)
            await route_task

            self.assertEqual(orchestrator.active_project.phase, "deploying")
            devops.receive.assert_awaited_once()
            deploy_task = devops.receive.await_args.args[0]
            self.assertEqual(deploy_task.origin, "ceo")
            self.assertEqual(deploy_task.target, "devops")
            self.assertEqual(deploy_task.payload["qa_report"], "QA passed")
            engineering.receive.assert_not_awaited()

            devops_deliverable = Deliverable(
                task_id="task-release-1",
                department="devops",
                artifact_type="deploy_report",
                payload={"deploy_url": "https://dashboard.example.com"},
                status="success",
                metadata={"context": {"project_name": "dashboard"}},
            )
            await orchestrator._route(devops_deliverable)
            self.assertEqual(orchestrator.active_project.phase, "done")

            orchestrator.active_project.phase = "qa"
            qa_deliverable.task_id = "task-release-2"
            route_task = asyncio.create_task(orchestrator._route(qa_deliverable))
            await asyncio.sleep(0)
            orchestrator.reject(seen_messages[-1].task_id, reason="Fix launch blocker")
            await route_task

            self.assertEqual(orchestrator.active_project.phase, "development")
            engineering.receive.assert_awaited_once()
            revision_task = engineering.receive.await_args.args[0]
            self.assertEqual(revision_task.origin, "ceo")
            self.assertEqual(revision_task.target, "engineering")
            self.assertEqual(revision_task.payload["qa_report"], "QA passed")
            self.assertEqual(
                revision_task.payload["ceo_feedback"], "Fix launch blocker"
            )

    async def test_submit_release_approval_routes_to_devops_not_engineering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            orchestrator.active_project = Project(
                project_id="project-submit-release",
                name="dashboard",
                phase="release_ready",
                engineering_result=Deliverable(
                    task_id="task-submit-release",
                    department="engineering",
                    artifact_type="code",
                    payload="implemented",
                    status="success",
                    metadata={"files": ["app.py"]},
                ),
            )
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
            )
            devops = DevOpsDepartment(project_memory=memory)
            engineering.receive = AsyncMock()
            devops.receive = AsyncMock()
            orchestrator.register(engineering)
            orchestrator.register(devops)

            release_task = CompanyTask(
                task_id="task-submit-release",
                origin="qa",
                target="engineering",
                artifact_type="test_report",
                payload={"qa_report": "QA passed", "qa_metadata": {"ok": True}},
                blocking=True,
                context={
                    "gate_name": "release_approval",
                    "handoff_to": "devops",
                    "project_name": "dashboard",
                },
            )

            submit_task = asyncio.create_task(orchestrator.submit(release_task))
            await asyncio.sleep(0)
            orchestrator.approve("task-submit-release")
            await submit_task

            self.assertEqual(orchestrator.active_project.phase, "deploying")
            engineering.receive.assert_not_awaited()
            devops.receive.assert_awaited_once()
            deploy_task = devops.receive.await_args.args[0]
            self.assertEqual(deploy_task.target, "devops")
            self.assertEqual(deploy_task.payload["engineering_result"], "implemented")
            self.assertEqual(deploy_task.payload["qa_report"], "QA passed")

    async def test_engineering_failure_returns_to_engineering_in_development(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            orchestrator.active_project = Project(
                project_id="project-3",
                name="dashboard",
                phase="development",
            )
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(return_value={"summary": "done"})
                ),
            )
            engineering.receive = AsyncMock()
            orchestrator.register(engineering)

            engineering_deliverable = Deliverable(
                task_id="task-failure-1",
                department="engineering",
                artifact_type="code",
                payload="implementation failed",
                status="failure",
                metadata={},
            )

            await orchestrator._route(engineering_deliverable)

            self.assertEqual(orchestrator.active_project.phase, "development")
            self.assertEqual(
                orchestrator.active_project.engineering_result, engineering_deliverable
            )
            engineering.receive.assert_awaited_once()
            retry_task = engineering.receive.await_args.args[0]
            self.assertEqual(retry_task.target, "engineering")
            self.assertIn(
                "Address the engineering failure", retry_task.payload["instruction"]
            )

    async def test_full_happy_path_prototype_to_release(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(tmpdir)
            orchestrator = CompanyOrchestrator(project_memory=memory)
            orchestrator.active_project = Project(
                project_id="happy-path-1",
                name="dashboard",
                phase="prototyping",
            )
            product = ProductDepartment(project_memory=memory)
            engineering = EngineeringDepartment(
                project_memory=memory,
                agent_loop=SimpleNamespace(
                    run=AsyncMock(
                        return_value={
                            "summary": "implemented dashboard",
                            "metadata": {
                                "files": ["app.py"],
                                "token_usage": {"total_tokens": 42},
                            },
                        }
                    )
                ),
            )
            qa = QADepartment(project_memory=memory)
            devops = DevOpsDepartment(project_memory=memory)
            departments = [product, engineering, qa, devops]
            for department in departments:
                orchestrator.register(department)

            async def auto_approve(message):
                if (
                    isinstance(message, EventMessage)
                    and message.event == CompanyEvent.APPROVAL_REQUIRED
                ):
                    asyncio.create_task(
                        orchestrator.handle_approval_response(
                            message.task_id, True, source="test"
                        )
                    )

            orchestrator.on_deliverable(auto_approve)
            loops = [
                asyncio.create_task(department.run_loop())
                for department in departments
            ]
            try:
                await orchestrator.submit(
                    CompanyTask(
                        task_id="happy-path-1",
                        origin="ceo",
                        target="product",
                        artifact_type="raw_prompt",
                        payload="Build an internal API endpoint",
                        blocking=False,
                        context={"project_name": "dashboard"},
                    )
                )

                async def project_done():
                    while orchestrator.active_project.phase != "done":
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(project_done(), timeout=5)
            finally:
                for loop in loops:
                    loop.cancel()
                await asyncio.gather(*loops, return_exceptions=True)

            self.assertEqual(orchestrator.active_project.phase, "done")
            self.assertIsNotNone(orchestrator.active_project.prd)
            self.assertEqual(
                orchestrator.active_project.engineering_result.status, "success"
            )
            self.assertEqual(orchestrator.active_project.qa_result.status, "success")
            self.assertEqual(
                orchestrator.active_project.deploy_result.status, "success"
            )
            observability = memory.data["observability"]
            self.assertGreaterEqual(
                observability["turns_per_phase"]["prototyping"]["product"], 1
            )
            self.assertEqual(
                observability["token_usage_per_department"]["engineering"], 42
            )
            status = orchestrator.company_status()
            self.assertIn("Company Dashboard", status)
            self.assertIn("Phase: done", status)
            self.assertIn("Token usage per department", status)

    def test_departments_do_not_import_each_other(self):
        import ast
        from pathlib import Path

        department_dir = Path("aider/company/departments")
        department_modules = {
            f"aider.company.departments.{path.stem}"
            for path in department_dir.glob("*.py")
            if path.stem != "__init__"
        }
        for path in department_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported = {node.module or ""}
                else:
                    continue
                forbidden = imported & (
                    department_modules - {f"aider.company.departments.{path.stem}"}
                )
                self.assertFalse(
                    forbidden, f"{path} imports departments directly: {forbidden}"
                )


if __name__ == "__main__":
    unittest.main()
