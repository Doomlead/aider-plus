from __future__ import annotations

import json

from aider.memory import MemoryQuery, MemoryRecord, MemoryStore, ProjectMemory


def test_memory_schema_migration_preserves_existing_project_data(tmp_path):
    memory_path = tmp_path / ".aider" / "project_memory.json"
    memory_path.parent.mkdir()
    memory_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "audit_log": [{"event_type": "kept"}],
                "playbook": {"coding_standards": ["preserve me"]},
                "skill_proposals": [{"proposal_id": "skill-1"}],
                "observability": {
                    "turns_per_phase": {"development": {"engineering": 1}},
                    "token_usage_per_department": {"engineering": 12},
                },
            }
        ),
        encoding="utf-8",
    )

    project_memory = ProjectMemory(str(tmp_path))
    data = project_memory.load()

    assert data["schema_version"] == 4
    assert data["audit_log"] == [{"event_type": "kept"}]
    assert data["playbook"]["coding_standards"] == ["preserve me"]
    assert data["skill_proposals"] == [{"proposal_id": "skill-1"}]
    assert data["observability"]["turns_per_phase"] == {
        "development": {"engineering": 1}
    }
    assert (
        data["observability"]["token_usage_per_department"]["engineering"][
            "total_tokens"
        ]
        == 12
    )
    assert data["memory"] == {"records": [], "threads": []}


def test_memory_store_appends_persists_and_queries_records(tmp_path):
    project_memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(project_memory)

    record = store.append_record(
        MemoryRecord(
            kind="decision",
            content="Use a local-first memory fabric for Phase 1.",
            scope="project",
            visibility="team",
            tags=["phase-1", "architecture"],
            metadata={"source": "test"},
        )
    )

    assert store.get_record(record.id).content == record.content
    matches = store.query_records(
        MemoryQuery(text="local-first", scope="project", tags=("phase-1",))
    )
    assert [item.id for item in matches] == [record.id]

    reloaded = ProjectMemory(str(tmp_path))
    reloaded.load()
    persisted = MemoryStore(reloaded).get_record(record.id)
    assert persisted is not None
    assert persisted.kind == "decision"
    assert persisted.metadata == {"source": "test"}


def test_memory_store_applies_basic_visibility_filtering(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    shared = store.append_record(
        MemoryRecord(content="shared rollout note", scope="shared", visibility="public")
    )
    engineering = store.append_record(
        MemoryRecord(
            content="engineering-only retry note",
            scope="role:engineering",
            visibility="team",
        )
    )
    product_private = store.append_record(
        MemoryRecord(
            content="private product discovery",
            scope="role:product",
            visibility="private",
            author="pm",
        )
    )

    engineering_view = store.query_records(
        MemoryQuery(requester_scope="role:engineering")
    )
    assert [record.id for record in engineering_view] == [shared.id, engineering.id]

    product_view = store.query_records(
        MemoryQuery(requester_scope="role:product", requester="pm")
    )
    assert [record.id for record in product_view] == [shared.id, product_private.id]

    public_only = store.query_records(MemoryQuery(visibility="public"))
    assert [record.id for record in public_only] == [shared.id]


def test_memory_record_serializes_skill_evidence_block(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    evidence = {
        "skill": "engineering/run-focused-tests",
        "task_id": "t1",
        "outcome": "success",
        "signals": {"tests": ["pytest tests/company/test_memory_fabric.py"]},
    }

    record = store.append_record(
        MemoryRecord(
            kind="skill_evidence",
            content="Focused tests helped validate the change.",
            scope="skill:engineering/run-focused-tests",
            visibility="skill",
            skill_evidence=evidence,
        )
    )

    serialized = store.get_record(record.id).to_dict()
    assert serialized["skill_evidence"] == evidence
    assert serialized["scope"] == "skill:engineering/run-focused-tests"

    reloaded = ProjectMemory(str(tmp_path))
    reloaded.load()
    loaded_record = MemoryStore(reloaded).get_record(record.id)
    assert loaded_record.skill_evidence == evidence


import asyncio

from aider.company.department import Department
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyTask
from aider.company.interfaces import Deliverable
from aider.memory import communication as communication_memory


class LedgerDepartment(Department):
    name = "ledger"

    async def process(self, task: CompanyTask) -> Deliverable:
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="memo",
            payload="done",
            status="success",
            metadata={"handoff_to": "next"},
        )


class NextLedgerDepartment(Department):
    name = "next"

    async def process(self, task: CompanyTask) -> Deliverable:
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="memo",
            payload="next done",
            status="success",
        )


def test_communication_helpers_create_standard_records(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))

    record = communication_memory.user_instruction(
        store,
        "Build the onboarding dashboard",
        surface="cli",
        session_id="session-1",
        task_id="task-1",
        origin="ceo",
        target="product",
    )

    assert record.kind == "user_instruction"
    assert record.scope == "thread:session-1"
    assert record.visibility == "team"
    assert record.metadata["event_type"] == "user_instruction"
    assert record.metadata["task_id"] == "task-1"
    assert record.metadata["thread_id"] == "session-1"
    assert record.metadata["origin"] == "ceo"
    assert record.metadata["targets"] == ["product"]


def test_department_process_records_task_and_deliverable_without_changing_result(
    tmp_path,
):
    memory = ProjectMemory(str(tmp_path))
    dept = LedgerDepartment(memory)
    task = CompanyTask(
        task_id="task-2",
        origin="ceo",
        target="ledger",
        artifact_type="raw_prompt",
        payload="make it so",
        context={"thread_id": "thread-2"},
    )

    deliverable = asyncio.run(dept.process(task))

    assert deliverable.payload == "done"
    records = MemoryStore(memory).query_records(tags=("communication",))
    assert [record.kind for record in records] == [
        "task_received",
        "deliverable_produced",
    ]
    assert records[0].scope == "department:ledger"
    assert records[0].metadata["origin"] == "ceo"
    assert records[0].metadata["targets"] == ["ledger"]
    assert records[0].metadata["thread_id"] == "thread-2"
    assert records[1].skill_evidence["task_id"] == "task-2"


def test_orchestrator_records_submit_route_and_handoff(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    orchestrator.register(LedgerDepartment(memory))
    task = CompanyTask(
        task_id="task-3",
        origin="ceo",
        target="ledger",
        artifact_type="raw_prompt",
        payload="ship it",
    )

    asyncio.run(orchestrator.submit(task))

    records = MemoryStore(memory).query_records(tags=("communication",))
    kinds = [record.kind for record in records]
    assert "route_decision" in kinds
    assert "handoff" in kinds
    route = next(record for record in records if record.kind == "route_decision")
    assert route.metadata["strategy"] == "submit"
    assert route.metadata["task_id"] == "task-3"
    handoff = next(record for record in records if record.kind == "handoff")
    assert handoff.metadata["origin"] == "ceo"
    assert handoff.metadata["targets"] == ["ledger"]


def test_orchestrator_records_deliverable_route_handoff_and_approval_decision(tmp_path):
    async def scenario():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        orchestrator.register(LedgerDepartment(memory))
        orchestrator.register(NextLedgerDepartment(memory))
        deliverable = Deliverable(
            task_id="task-4",
            department="ledger",
            artifact_type="memo",
            payload="handoff payload",
            status="success",
            metadata={"handoff_to": "next"},
        )
        await orchestrator._route(deliverable)

        approval_task = CompanyTask(
            task_id="approval-1",
            origin="product",
            target="ledger",
            artifact_type="prd",
            payload="approve this",
            blocking=True,
        )
        waiter = asyncio.create_task(
            orchestrator.approvals.create_request(approval_task)
        )
        await asyncio.sleep(0)
        resolved = await orchestrator.handle_approval_response(
            "approval-1", True, source="discord", metadata={"reviewer": "ceo"}
        )
        decision = await waiter
        orchestrator.approvals.close_request("approval-1")
        return memory, resolved, decision

    memory, resolved, decision = asyncio.run(scenario())

    assert resolved is True
    assert decision.approved is True
    records = MemoryStore(memory).query_records(tags=("communication",))
    kinds = [record.kind for record in records]
    assert "route_decision" in kinds
    assert "handoff" in kinds
    assert "approval_decision" in kinds
    approval = next(record for record in records if record.kind == "approval_decision")
    assert approval.metadata["approved"] is True
    assert approval.metadata["approved_by"] == "discord"
    assert approval.metadata["targets"] == ["ledger"]
