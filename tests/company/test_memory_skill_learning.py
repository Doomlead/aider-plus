from __future__ import annotations

from aider.company.orchestrator import CompanyOrchestrator
from aider.company.project import Project
from aider.company.self_improvement import SelfImprovementService
from aider.company.skills import CompanySkillManager, SkillLearningConfig, SkillProposal
from aider.memory import MemoryRecord, MemoryStore, ProjectMemory
from aider.memory.evidence import collect_evidence_for_project


def _memory_record(
    task_id: str, content: str = "Run focused tests after editing retry logic"
) -> MemoryRecord:
    return MemoryRecord(
        kind="deliverable_produced",
        content=content,
        scope="department:engineering",
        visibility="team",
        tags=["communication", "deliverable_produced"],
        metadata={
            "department": "engineering",
            "artifact_type": "code",
            "status": "success",
            "task_id": task_id,
            "thread_id": "retry-thread",
        },
        skill_evidence={
            "task_id": task_id,
            "role": "engineering",
            "outcome": "success",
            "signals": {"tests": ["pytest tests/test_retry.py"]},
        },
    )


def test_memory_evidence_cluster_creation(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    first = store.append_record(_memory_record("t1"))
    second = store.append_record(
        _memory_record("t2", "Reuse the focused retry test loop")
    )
    store.append_record(
        MemoryRecord(
            kind="deliverable_produced",
            content="failed rollout",
            scope="department:devops",
            metadata={
                "department": "devops",
                "status": "failure",
                "thread_id": "deploy",
            },
            skill_evidence={"role": "devops", "outcome": "failure"},
        )
    )
    project = Project(project_id="p1", name="Demo", phase="post_mortem")

    clusters = collect_evidence_for_project(project, store, min_records=2)

    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.department == "engineering"
    assert cluster.channel == "code"
    assert cluster.thread_id == "retry-thread"
    assert cluster.outcome == "success"
    assert cluster.source_memory_records == [first.id, second.id]
    assert cluster.source_tasks == ["t1", "t2"]
    assert cluster.suggested_scope == "engineering"
    assert cluster.procedure_steps


def test_proposal_generation_from_memory_keeps_evidence_and_steps_pending(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    first = store.append_record(_memory_record("t1"))
    second = store.append_record(
        _memory_record("t2", "Validate retry branch with focused tests")
    )
    orchestrator = CompanyOrchestrator(
        memory,
        company_config=None,
    )
    project = Project(project_id="p1", name="Demo", phase="post_mortem")

    proposals = SelfImprovementService(
        orchestrator.state, SkillLearningConfig(min_successful_repetitions=2)
    ).learn_from_memory(project)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.status == "pending"
    assert proposal.scope == "engineering"
    assert proposal.source_memory_records == [first.id, second.id]
    assert proposal.source_tasks == ["t1", "t2"]
    assert proposal.procedure_steps
    assert (
        proposal.outcome_summary and "2 success engineering" in proposal.outcome_summary
    )
    assert proposal.suggested_scope == "engineering"
    assert "Evidence from memory records" in proposal.content
    assert memory.data["skill_proposals"][-1]["status"] == "pending"
    assert not (
        tmp_path / ".aider" / "skills" / proposal.scope / proposal.name / "SKILL.md"
    ).exists()


def test_memory_learning_deduplicates_against_existing_skills_and_proposals(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    store.append_record(_memory_record("t1"))
    store.append_record(_memory_record("t2"))
    orchestrator = CompanyOrchestrator(memory)
    project = Project(project_id="p1", name="Demo", phase="post_mortem")
    service = SelfImprovementService(
        orchestrator.state, SkillLearningConfig(min_successful_repetitions=2)
    )
    first = service.learn_from_memory(project)

    second = service.learn_from_memory(project)

    assert len(first) == 1
    assert second == []

    # Existing approved skill with the same scope/name also blocks a new pending proposal.
    proposal = first[0]
    CompanySkillManager(orchestrator.state).manager.create_skill(
        scope=proposal.scope,
        name=proposal.name,
        content=proposal.content,
    )
    duplicate = SkillProposal(
        proposal_id="duplicate",
        action="create",
        scope=proposal.scope,
        name=proposal.name,
        title=proposal.title,
        content=proposal.content,
        rationale="duplicate",
    )
    assert service._store_new_proposals([duplicate]) == []


def test_memory_learning_does_not_auto_create_unless_configured(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    store.append_record(_memory_record("t1"))
    store.append_record(_memory_record("t2"))
    orchestrator = CompanyOrchestrator(memory)
    project = Project(project_id="p1", name="Demo", phase="post_mortem")

    proposal = SelfImprovementService(
        orchestrator.state,
        SkillLearningConfig(
            min_successful_repetitions=2,
            auto_create=True,
            require_human_approval=True,
        ),
    ).learn_from_memory(project)[0]

    assert proposal.status == "pending"
    assert not (
        tmp_path / ".aider" / "skills" / proposal.scope / proposal.name / "SKILL.md"
    ).exists()

    memory2 = ProjectMemory(str(tmp_path / "configured"))
    store2 = MemoryStore(memory2)
    store2.append_record(_memory_record("t1"))
    store2.append_record(_memory_record("t2"))
    orchestrator2 = CompanyOrchestrator(memory2)
    proposal2 = SelfImprovementService(
        orchestrator2.state,
        SkillLearningConfig(
            min_successful_repetitions=2,
            auto_create=True,
            require_human_approval=False,
        ),
    ).learn_from_memory(project)[0]

    assert proposal2.status == "approved"
    assert (
        tmp_path
        / "configured"
        / ".aider"
        / "skills"
        / proposal2.scope
        / proposal2.name
        / "SKILL.md"
    ).exists()
