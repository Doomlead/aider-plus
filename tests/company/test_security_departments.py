from __future__ import annotations

import asyncio

from aider.company.department import Department
from aider.company.coo import NanobotCOO
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import (
    CompanyTask,
    Deliverable,
    SecurityPatchRequest,
    SecurityScanResult,
)
from aider.memory import ProjectMemory


class EchoEngineeringDepartment(Department):
    name = "engineering"

    def __init__(self, memory):
        super().__init__(memory)
        self.received = []

    async def process(self, task: CompanyTask) -> Deliverable:
        self.received.append(task)
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="code",
            payload=task.payload,
            status="success",
            metadata={"context": task.context},
        )


def test_security_schema_round_trips():
    result = SecurityScanResult(
        scan_type="platform_audit",
        severity="critical",
        findings=[
            {
                "location": "mcp",
                "description": "unsafe tool",
                "recommendation": "gate it",
            }
        ],
        risk_score=9.8,
    )
    patch = SecurityPatchRequest(
        finding_id="sec-1",
        patch_plan="Add policy enforcement.",
        target_department="architect",
        urgency="immediate",
    )

    assert result.to_dict()["severity"] == "critical"
    assert result.to_dict()["findings"][0]["location"] == "mcp"
    assert patch.to_dict()["urgency"] == "immediate"


def test_orchestrator_routes_critical_security_scan_to_engineering(tmp_path):
    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        engineering = EchoEngineeringDepartment(memory)
        orchestrator.register(engineering)

        scan = Deliverable(
            task_id="scan-1",
            department="security_platform",
            artifact_type="security_scan_result",
            payload={
                "scan_type": "platform_audit",
                "severity": "critical",
                "findings": [
                    {
                        "id": "finding-1",
                        "location": "daemon",
                        "description": "daemon runs unsafe tools",
                        "recommendation": "route through approval policy",
                    }
                ],
                "risk_score": 10.0,
            },
            status="needs_review",
            metadata={"context": {"scan_type": "platform_audit"}},
        )

        routed = await orchestrator._route_security_scan_result(scan)
        queued = await engineering.inbox.get()

        assert routed is True
        assert queued.artifact_type == "security_patch_request"
        assert queued.target == "engineering"
        assert queued.context["security_patch_request"]["urgency"] == "immediate"
        assert memory.data["security"]["status"] == "red"

    asyncio.run(run())


def test_coo_routes_run_security_scan_to_appsec(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    orchestrator.register(EchoEngineeringDepartment(memory))
    security = EchoEngineeringDepartment(memory)
    security.name = "security_app"
    orchestrator.register(security)
    coo = NanobotCOO(orchestrator=orchestrator, default_target="engineering")
    session = coo.session_manager.get_or_create("test:security-route")

    decision = asyncio.run(coo.decide_action("Run security scan", session))

    assert decision.action == "delegate_company_task"
    assert decision.company_target == "security_app"
