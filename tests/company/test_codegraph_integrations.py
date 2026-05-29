from __future__ import annotations

import asyncio

from aider.company.departments.delivery import DeliveryDepartment
from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.qa import QADepartment
from aider.company.schemas import CompanyTask, Deliverable
from aider.memory import ProjectMemory


class FakeToolRegistry:
    def set_department(self, department):
        self.department = department


class FakeAgentLoop:
    def __init__(self):
        self.tool_registry = FakeToolRegistry()


def test_qa_runs_codegraph_affected_tests(tmp_path):
    test_file = tmp_path / "tests" / "test_service.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    department = QADepartment(ProjectMemory(str(tmp_path)))
    task = CompanyTask(
        task_id="qa-codegraph",
        origin="engineering",
        target="qa",
        artifact_type="implementation",
        payload={"engineering_metadata": {"files": ["service.py"]}},
        context={
            "codegraph": {
                "affected_tests": {
                    "changed_files": ["service.py"],
                    "affected_tests": ["tests/test_service.py"],
                    "impacted_files": ["service.py", "tests/test_service.py"],
                }
            }
        },
    )

    deliverable = asyncio.run(department.process(task))

    assert deliverable.payload["test_passed"] is True
    assert deliverable.payload["targeted_by_codegraph"] == ["tests/test_service.py"]
    assert deliverable.metadata["test_coverage"] == "executed"


def test_reviewer_feedback_includes_codegraph_impact(tmp_path):
    department = EngineeringDepartment(ProjectMemory(str(tmp_path)), FakeAgentLoop())
    previous = Deliverable(
        task_id="eng-1",
        department="engineering",
        artifact_type="implementation",
        payload={},
        status="success",
        metadata={},
    )

    feedback = department._build_review_feedback(
        previous_deliverable=previous,
        changed_files=["service.py"],
        diff="diff --git a/service.py b/service.py",
        checks=[],
        context={
            "prd_content": "Ship it",
            "codegraph": {
                "impact": {
                    "files": [{"path": "service.py"}, {"path": "api.py"}],
                    "routes": [{"method": "GET", "route": "/hello"}],
                }
            },
        },
    )

    assert any(
        "Code graph impact analysis" in item for item in feedback["what_is_good"]
    )
    assert any("GET /hello" in item for item in feedback["what_is_good"])
    assert any("Code graph review scope" in item for item in feedback["concerns"])
    assert feedback["summary"] == "Approved for QA."


def test_delivery_risk_assessment_includes_codegraph_impact(tmp_path):
    department = DeliveryDepartment(ProjectMemory(str(tmp_path)))
    task = CompanyTask(
        task_id="delivery-codegraph",
        origin="qa",
        target="delivery",
        artifact_type="test_report",
        payload={
            "engineering_result": {"summary": "done"},
            "qa_report": {"summary": "passed"},
            "qa_metadata": {"test_coverage": "executed"},
        },
        context={
            "project_name": "Routes",
            "codegraph": {
                "impact": {
                    "files": [{"path": "service.py"}, {"path": "api.py"}],
                    "routes": [{"route": "/hello"}],
                }
            },
        },
    )

    deliverable = asyncio.run(department.process(task))

    risk_ids = {risk["risk_id"] for risk in deliverable.metadata["risks"]}
    assert "RISK-CODEGRAPH-IMPACT" in risk_ids
    assert deliverable.status == "success"
