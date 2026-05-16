from __future__ import annotations

import asyncio

from aider.company.department import Department
from aider.company.interfaces import Deliverable
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.project import Project
from aider.company.schemas import CompanyTask
from aider.memory import ProjectMemory


class CaptureDepartment(Department):
    def __init__(self, name: str, memory: ProjectMemory):
        self.name = name
        super().__init__(memory)

    async def process(self, task: CompanyTask) -> Deliverable:
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type=task.artifact_type,
            payload=task.payload,
            status="success",
        )


def test_orchestrator_routes_delivery_after_product_before_engineering(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    orchestrator.active_project = Project(
        project_id="p1", name="Demo", phase="prototyping"
    )
    delivery = CaptureDepartment("delivery", memory)
    engineering = CaptureDepartment("engineering", memory)
    orchestrator.register(delivery)
    orchestrator.register(engineering)

    product_deliverable = Deliverable(
        task_id="t1",
        department="product",
        artifact_type="prd",
        payload="# PRD",
        status="success",
        metadata={"handoff_to": "engineering", "context": {"project_name": "Demo"}},
    )

    asyncio.run(orchestrator._route_project_state(product_deliverable))

    delivery_task = delivery.inbox.get_nowait()
    engineering_task = engineering.inbox.get_nowait()
    assert delivery_task.target == "delivery"
    assert delivery_task.origin == "product"
    assert delivery_task.context["project_phase"] == "prototyping"
    assert engineering_task.target == "engineering"


def test_orchestrator_blocks_devops_when_delivery_handoff_not_ready(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    orchestrator.active_project = Project(
        project_id="p1", name="Demo", phase="delivery"
    )
    orchestrator.register(CaptureDepartment("delivery", memory))
    events = []

    async def capture(event):
        events.append(event)

    orchestrator.on_deliverable(capture)
    release_task = CompanyTask(
        task_id="release-1",
        origin="delivery",
        target="engineering",
        artifact_type="test_report",
        payload={
            "delivery_handover": {
                "ready_for_devops": False,
                "critical_blockers": ["qa_report"],
            }
        },
        context={"gate_name": "release_approval"},
    )

    ready = asyncio.run(orchestrator._handle_delivery_handoff(release_task))

    assert ready is False
    assert orchestrator.active_project.phase == "delivery"
    assert events[-1].payload["critical_blockers"] == ["qa_report"]
