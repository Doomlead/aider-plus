from __future__ import annotations

import json

from aider.company.config import CompanyConfig
from aider.company.context import ContextBuilder
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.project import Project
from aider.company.schemas import CompanyTask, Deliverable
from aider.company.self_improvement import SelfImprovementService
from aider.company.skills import CompanySkillManager, SkillLearningConfig, SkillProposal
from aider.memory import ProjectMemory
from aider.skills import SkillManager


def test_skill_manager_rejects_path_escape_and_queries_role_skill(tmp_path):
    manager = SkillManager(tmp_path / ".aider" / "skills")
    manager.create_skill(
        scope="engineering",
        name="add-cli-command",
        content=(
            "# Add CLI Command\n"
            "Description: Add parser commands and regression tests for CLI workflows.\n"
        ),
    )

    matches = manager.query_skills(
        "please add a CLI parser command with tests",
        scopes=["engineering"],
    )

    assert [match.name for match in matches] == ["add-cli-command"]
    try:
        manager.write_skill_file("engineering", "add-cli-command", "../escape.txt", "bad")
    except ValueError as err:
        assert "escapes" in str(err)
    else:
        raise AssertionError("path escape should be rejected")


def test_context_builder_injects_role_scoped_skill_guidance(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    state = CompanyOrchestrator(memory).state
    skill_manager = CompanySkillManager(state)
    skill_manager.manager.create_skill(
        scope="engineering",
        name="run-focused-tests",
        content="# Run Focused Tests\nDescription: Run focused pytest checks after code edits.\n",
    )

    task = CompanyTask(
        task_id="t1",
        origin="ceo",
        target="engineering",
        artifact_type="code",
        payload="edit code and run pytest checks",
    )
    context = ContextBuilder(state).build(task, ["skills.engineering"])

    assert context["skills"][0]["name"] == "run-focused-tests"
    assert "engineering/run-focused-tests" in context["skill_guidance"][0]


def test_skill_proposals_are_approval_gated_and_can_be_approved(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    state = CompanyOrchestrator(memory).state
    manager = CompanySkillManager(state)
    proposal = SkillProposal(
        proposal_id="skill-engineering-test",
        action="create",
        scope="engineering",
        name="safe-code-edit",
        title="Safe Code Edit",
        content="# Safe Code Edit\nDescription: Edit code safely and run tests.\n",
        rationale="Repeated success",
        source_tasks=["t1", "t2"],
        confidence=0.8,
    )

    path = manager.create_proposal(proposal)
    assert json.loads(path.read_text())["status"] == "pending"
    skill_file = (
        tmp_path / ".aider" / "skills" / "engineering" / "safe-code-edit" / "SKILL.md"
    )
    assert not skill_file.exists()

    approved = manager.approve_proposal("skill-engineering-test")

    assert approved.status == "approved"
    assert skill_file.exists()
    assert memory.data["skill_proposals"][-1]["status"] == "approved"


def test_self_improvement_adds_skill_proposals_without_replacing_playbooks(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    config = CompanyConfig(
        skill_learning=SkillLearningConfig(min_successful_repetitions=2)
    )
    orchestrator = CompanyOrchestrator(memory, company_config=config)
    project = Project(project_id="p1", name="Demo", phase="post_mortem")
    orchestrator.active_project = project

    for task_id in ("t1", "t2"):
        orchestrator.state.append_audit_event(
            department="engineering",
            event_type="deliverable_produced",
            payload={"summary": "implemented workflow"},
            metadata={"task_id": task_id, "status": "success"},
        )
    memory.update({"playbook": {"coding_standards": ["keep existing lesson"]}})
    memory.persist()

    proposals = SelfImprovementService(
        orchestrator.state, config.skill_learning
    ).learn_from_post_mortem(
        project,
        Deliverable(
            task_id="t2",
            department="devops",
            artifact_type="deploy_request",
            payload="done",
            status="success",
        ),
    )

    assert len(proposals) == 1
    assert proposals[0].scope == "engineering"
    assert memory.data["playbook"]["coding_standards"] == ["keep existing lesson"]
    assert memory.data["skill_proposals"][-1]["status"] == "pending"
