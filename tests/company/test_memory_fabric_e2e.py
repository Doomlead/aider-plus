from __future__ import annotations

from aider.company.interfaces import Deliverable
from aider.company.project import Project
from aider.company.schemas import CompanyTask
from aider.company.self_improvement import SelfImprovementService
from aider.company.skills import CompanySkillManager, SkillLearningConfig
from aider.company.state import CompanyStateManager
from aider.memory import ProjectMemory
from aider.memory.communication import (
    approval_decision,
    append_communication_record,
    handoff,
    user_instruction,
)
from aider.memory.evidence import collect_evidence_for_project
from aider.memory.store import MemoryStore


def test_phase1_memory_fabric_full_cycle_from_instruction_to_next_task_recall(tmp_path):
    project_memory = ProjectMemory(str(tmp_path))
    state = CompanyStateManager(project_memory)
    project = Project(project_id="phase1", name="Phase 1 Fabric")
    state.active_project = project
    store = MemoryStore(project_memory)
    config = SkillLearningConfig(min_successful_repetitions=2)

    instruction = user_instruction(
        store,
        "When QA validates migrations, include rollback evidence before approval.",
        surface="cli",
        session_id="thread-phase1",
        task_id="task-phase1",
        target="qa",
    )
    task = CompanyTask(
        task_id="task-phase1",
        origin="product",
        target="qa",
        artifact_type="test_report",
        payload="Validate v5 migration rollback evidence and approval notes.",
        context={"thread_id": "thread-phase1"},
    )
    handoff_record = handoff(
        store, task, source="product", reason="QA owns migration validation"
    )

    first = append_communication_record(
        store,
        "deliverable_produced",
        "QA verified migration rollback evidence before approval.",
        scope="department:qa",
        visibility="project",
        task_id="task-phase1",
        thread_id="thread-phase1",
        origin="qa",
        targets=["delivery"],
        metadata={"department": "qa", "channel": "qa-migration-approval"},
        skill_evidence={
            "task_id": "task-phase1",
            "role": "qa",
            "outcome": "success",
            "signals": {"checks": ["migration", "rollback", "approval"]},
        },
    )
    second = append_communication_record(
        store,
        "deliverable_produced",
        "QA attached rollback proof and approval summary for the handoff.",
        scope="department:qa",
        visibility="project",
        task_id="task-phase1-repeat",
        thread_id="thread-phase1",
        origin="qa",
        targets=["delivery"],
        metadata={"department": "qa", "channel": "qa-migration-approval"},
        skill_evidence={
            "task_id": "task-phase1-repeat",
            "role": "qa",
            "outcome": "success",
            "signals": {"checks": ["rollback proof", "approval summary"]},
        },
    )
    approval = approval_decision(
        store,
        task_id="task-phase1",
        approved=True,
        source="ceo",
        reason="Evidence-backed QA workflow approved.",
        task=task,
    )

    assert instruction is not None
    assert handoff_record is not None
    assert first is not None and second is not None
    assert approval is not None
    assert store.get_metrics()["skill_evidence_coverage_pct"] > 0

    clusters = collect_evidence_for_project(project, store, min_records=2)
    assert clusters
    assert clusters[0].source_memory_records == [first.id, second.id]

    proposals = SelfImprovementService(state, config).learn_from_memory(project)
    assert proposals
    proposal = proposals[0]
    assert proposal.source_memory_records == [first.id, second.id]

    skills = CompanySkillManager(state, config)
    approved = skills.approve_proposal(proposal.proposal_id)
    assert approved.status == "approved"

    next_task = Deliverable(
        task_id="task-next",
        department="qa",
        artifact_type="test_report",
        payload="Recall the migration approval rollback evidence workflow for another QA handoff.",
        status="success",
    )
    recall_task = CompanyTask(
        task_id=next_task.task_id,
        origin="delivery",
        target="qa",
        artifact_type="test_report",
        payload=next_task.payload,
        context={"thread_id": "thread-next"},
    )
    recalled = skills.query_for_task(recall_task, role="qa")
    guidance = skills.format_skill_guidance(recalled)

    assert recalled
    assert any(approved.name == skill.name for skill in recalled)
    assert any(
        "migration" in item.lower() or "rollback" in item.lower() for item in guidance
    )
