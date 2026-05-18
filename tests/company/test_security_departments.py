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
    assert "# Security Report" in result.to_markdown()
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


def test_security_patch_request_carries_full_patch_context():
    patch = SecurityPatchRequest(
        finding_id="sec-2",
        patch_plan="Validate tool policy before execution.",
        target_department="engineering",
        urgency="immediate",
        vulnerability_description="Unsafe tool execution path.",
        recommended_fix="Add policy validation before dispatch.",
        suggested_code_change_summary="Guard daemon tool dispatch with the approval policy.",
        architect_prompt_seed="Create an Engineering-ready patch plan with regression tests.",
        full_context={"finding": {"location": "daemon"}},
    )

    data = patch.to_dict()

    assert data["vulnerability_description"] == "Unsafe tool execution path."
    assert data["recommended_fix"] == "Add policy validation before dispatch."
    assert data["full_context"]["finding"]["location"] == "daemon"


def test_orchestrator_marks_security_patch_completion_and_backoff(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    memory.data["security"] = {
        "status": "red",
        "patch_in_progress": True,
        "security_scan_backoff_minutes": 120,
    }

    deliverable = Deliverable(
        task_id="patch-1",
        department="engineering",
        artifact_type="code",
        payload={"summary": "patched"},
        status="success",
        metadata={"context": {"security_patch_request": {"finding_id": "finding-1"}}},
    )

    orchestrator._record_security_patch_completion(deliverable)

    security = memory.data["security"]
    assert security["patch_in_progress"] is False
    assert security["recent_patches_applied"][-1]["finding_id"] == "finding-1"
    assert security["next_scan_at"]


def test_platformsec_excludes_agent_self_patch_requests(tmp_path):
    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        engineering = EchoEngineeringDepartment(memory)
        orchestrator.register(engineering)

        scan = Deliverable(
            task_id="scan-agent-loop",
            department="security_platform",
            artifact_type="security_scan_result",
            payload={
                "scan_type": "platform_audit",
                "severity": "critical",
                "findings": [
                    {
                        "id": "agent-loop",
                        "location": "aider/agent/loop.py",
                        "description": "agent-related self patch risk",
                        "recommendation": "patch PlatformSec routing",
                    },
                    {
                        "id": "aider-state",
                        "location": ".aider/company/run-state.json",
                        "description": "state-file issue",
                        "recommendation": "patch .aider state",
                    },
                ],
            },
            status="needs_review",
            metadata={"context": {"scan_type": "platform_audit"}},
        )

        routed = await orchestrator._route_security_scan_result(scan)

        assert routed is True
        assert engineering.inbox.empty()
        assert memory.data["security"]["status"] == "red"

    asyncio.run(run())


def test_security_posture_trend_is_recorded(tmp_path):
    async def run():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        first = Deliverable(
            task_id="scan-1",
            department="security_app",
            artifact_type="security_scan_result",
            payload={
                "scan_type": "vuln",
                "severity": "critical",
                "findings": [],
                "risk_score": 9.0,
            },
            status="needs_review",
            metadata={},
        )
        second = Deliverable(
            task_id="scan-2",
            department="security_app",
            artifact_type="security_scan_result",
            payload={
                "scan_type": "vuln",
                "severity": "low",
                "findings": [],
                "risk_score": 1.0,
            },
            status="success",
            metadata={},
        )

        await orchestrator._route_security_scan_result(first)
        await orchestrator._route_security_scan_result(second)

        assert memory.data["security"]["posture_trend"] == "improving"

    asyncio.run(run())
