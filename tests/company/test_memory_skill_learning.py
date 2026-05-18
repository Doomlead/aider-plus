from __future__ import annotations

from aider.company.orchestrator import CompanyOrchestrator
from aider.company.coo import NanobotCOO
from aider.company.project import Project
from aider.company.self_improvement import SelfImprovementService
from aider.company.skills import CompanySkillManager, SkillLearningConfig, SkillProposal
from aider.company.recall import RecallEngine
from aider.company.schemas import CompanyTask
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


def test_reinforcement_changes_ranking_and_explanations(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    strong = store.append_record(
        _memory_record("t1", "retry test loop for api timeout handling")
    )
    weak = store.append_record(_memory_record("t2", "generic note"))
    store.reinforce_record(strong.id, delta=3)
    store.reinforce_record(weak.id, delta=-1)
    task = CompanyTask(
        task_id="t-qa",
        origin="coo",
        target="engineering",
        artifact_type="code",
        payload={"description": "api timeout retry testing"},
    )
    ranked = RecallEngine(store).build_recall_packet(task).department_private
    assert ranked
    assert ranked[0]["id"] == strong.id
    assert "Reinforcement:" in ranked[0]["why_included"]


def test_reinforcement_and_decay_reduce_salience_without_deleting(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    record = store.append_record(_memory_record("t1"))
    service = SelfImprovementService(CompanyOrchestrator(memory).state)
    service.record_outcome(
        skill_name="retry-workflow",
        scope="engineering",
        task_id="t1",
        outcome="failure",
        supporting_memory_ids=[record.id],
    )
    service.record_outcome(
        skill_name="retry-workflow",
        scope="engineering",
        task_id="t2",
        outcome="success",
        supporting_memory_ids=[record.id],
    )

    # force staleness
    rec = memory.data["memory"]["records"][0]
    rec["created_at"] = "2000-01-01T00:00:00+00:00"
    memory.persist()
    result = service.apply_reinforcement_and_decay(threshold_days=1)
    refreshed = store.get_record(record.id)

    assert result["decayed_records"] >= 1
    assert refreshed is not None
    assert int((refreshed.metadata or {}).get("decay_count") or 0) >= 1

from aider.company.context import ContextBuilder
from aider.memory.evidence import cluster_channel_patterns


def _channel_record(task_id: str, content: str, *, scope: str = "channel:engineering:qa") -> MemoryRecord:
    return MemoryRecord(
        kind="deliverable_produced",
        content=content,
        scope=scope,
        visibility="team",
        metadata={
            "department": "engineering",
            "status": "success",
            "task_id": task_id,
            "channel_id": "engineering:qa",
        },
        skill_evidence={"task_id": task_id, "role": "engineering", "outcome": "success"},
    )


def test_channel_pattern_clustering(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    store.append_record(_channel_record("c1", "Failure report from QA with repro"))
    store.append_record(_channel_record("c2", "Targeted test added then minimal fix"))
    store.append_record(_channel_record("c3", "Verification passed in QA"))

    clusters = cluster_channel_patterns(store, "engineering:qa", min_records=2)

    assert len(clusters) == 1
    assert clusters[0].suggested_scope == "shared"
    assert clusters[0].procedure_steps


def test_channel_pattern_proposal_generation_and_dedup(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    store.append_record(_channel_record("c1", "Failure report from QA with repro"))
    store.append_record(_channel_record("c2", "Targeted test and minimal fix"))
    store.append_record(_channel_record("c3", "Verification passed"))
    orchestrator = CompanyOrchestrator(memory)
    project = Project(project_id="p1", name="Demo", phase="post_mortem")
    service = SelfImprovementService(orchestrator.state, SkillLearningConfig(min_successful_repetitions=2))

    first = service.learn_from_post_mortem(project, final_deliverable={})
    second = service.learn_from_post_mortem(project, final_deliverable={})

    channel = [p for p in first if p.metadata.get("source") == "channel"]
    assert channel
    assert channel[0].metadata.get("channel_scope") == "engineering:qa"
    assert second == []


def test_channel_scoped_visibility_engineering_yes_ux_no(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    record = store.append_record(_channel_record("c1", "Failure report -> test -> minimal fix -> verification"))
    eng_task = CompanyTask(task_id="e1", origin="coo", target="engineering", artifact_type="code", payload={"description": "eng qa loop test", "channel_id": "engineering:qa"})
    ux_task = CompanyTask(task_id="u1", origin="coo", target="ux", artifact_type="design", payload={"description": "retry"})

    eng_packet = RecallEngine(store).build_recall_packet(eng_task)
    assert any(item["id"] == record.id for item in eng_packet.channel)

    ux_packet = RecallEngine(store).build_recall_packet(ux_task)
    assert not any(item["id"] == record.id for item in ux_packet.department_private)

    mgr = CompanySkillManager(CompanyOrchestrator(memory).state)
    mgr.manager.create_skill(
        scope="shared",
        name="eng-qa-loop",
        content="# Eng QA loop\nDescription: test",
        metadata={"channel_scope": "engineering:qa"},
    )
    eng_skills = ContextBuilder(CompanyOrchestrator(memory).state)._get_relevant_skills(eng_task, ["skills.*"])
    ux_skills = ContextBuilder(CompanyOrchestrator(memory).state)._get_relevant_skills(ux_task, ["skills.*"])
    assert any(skill.name == "eng-qa-loop" for skill in eng_skills)
    assert not any(skill.name == "eng-qa-loop" for skill in ux_skills)


def test_patch_and_retirement_proposals_from_reinforcement(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    service = SelfImprovementService(orchestrator.state)
    service.record_outcome(skill_name="stale-skill", scope="engineering", task_id="a1", outcome="failure")
    service.record_outcome(skill_name="stale-skill", scope="engineering", task_id="a2", outcome="failure")
    service.record_outcome(skill_name="stale-skill", scope="engineering", task_id="a3", outcome="failure")
    service.record_outcome(skill_name="conflicting-skill", scope="engineering", task_id="b1", outcome="failure")
    service.record_outcome(skill_name="conflicting-skill", scope="engineering", task_id="b2", outcome="failure")
    service.record_outcome(skill_name="conflicting-skill", scope="engineering", task_id="b3", outcome="success")
    service.record_outcome(skill_name="conflicting-skill", scope="engineering", task_id="b4", outcome="success")
    project = Project(project_id="p1", name="Demo", phase="post_mortem")
    proposals = service.learn_from_memory(project)
    assert any(p.action == "retire" and p.target_skill_id == "engineering:stale-skill" for p in proposals)
    assert any(p.action == "patch" and p.target_skill_id == "engineering:conflicting-skill" for p in proposals)


def test_retirement_approval_archives_not_deletes(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    manager = CompanySkillManager(orchestrator.state)
    manager.manager.create_skill(scope="engineering", name="legacy-flow", content="# Legacy\nold")
    proposal = SkillProposal(
        proposal_id="retire-1",
        action="retire",
        scope="engineering",
        name="legacy-flow",
        title="retire",
        content="retire",
        rationale="bad outcomes",
        target_skill_id="engineering:legacy-flow",
        retirement_reason="repeated failures",
    )
    manager.create_proposal(proposal)
    approved = manager.approve_retire_proposal("retire-1", "approved")
    assert approved.status == "approved"
    archived = memory.data["skills"]["archived"]["engineering:legacy-flow"]
    assert archived["inactive"] is True
    assert (tmp_path / ".aider" / "skills" / "engineering" / "legacy-flow" / "SKILL.md").exists()


def test_recall_filters_retired_skills(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    retired = store.append_record(
        MemoryRecord(kind="note", content="old", scope="skill:engineering", metadata={"skill_id": "engineering:legacy-flow"})
    )
    active = store.append_record(
        MemoryRecord(kind="note", content="new", scope="skill:engineering", metadata={"skill_id": "engineering:new-flow"})
    )
    memory.data.setdefault("skills", {})["archived"] = {"engineering:legacy-flow": {"inactive": True}}
    task = CompanyTask(task_id="r1", origin="coo", target="engineering", artifact_type="code", payload={"description": "flow"})
    skills = RecallEngine(store).build_recall_packet(task).skills
    ids = [item["id"] for item in skills]
    assert active.id in ids
    assert retired.id not in ids


def test_coo_review_proposal_flow(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    proposal = SkillProposal(
        proposal_id="skill-eng-retry",
        action="create",
        scope="engineering",
        name="retry",
        title="Retry",
        content="# Retry",
        rationale="repeat success",
    )
    CompanySkillManager(orchestrator.state).create_proposal(proposal)
    coo = NanobotCOO(orchestrator=orchestrator)
    result = coo.review_proposal("skill-eng-retry", decision="approve")
    assert result["status"] == "approved"
    attention = coo.list_skills_needing_attention()
    assert "skills_needing_patch" in attention
