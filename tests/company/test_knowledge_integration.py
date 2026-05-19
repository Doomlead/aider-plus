from __future__ import annotations

from aider.company.context import ContextBuilder
from aider.company.departments.devops import DevOpsDepartment
from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.product import ProductDepartment
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyTask
from aider.company.knowledge import KnowledgeManager
from aider.company.skills import CompanySkillManager, SkillProposal
from aider.memory import ProjectMemory


class FakeAgentLoop:
    def __init__(self):
        self.model = None
        self.enable_prompt_caching = False


def create_company_skills(state):
    manager = CompanySkillManager(state)
    manager.manager.create_skill(
        scope="shared",
        name="accessible-rollout-checklist",
        content=(
            "# Accessible Rollout Checklist\n"
            "Description: Use for invite rollout work with accessibility, tests, and rollback.\n"
        ),
    )
    manager.manager.create_skill(
        scope="product",
        name="invite-prd-scope",
        content=(
            "# Invite PRD Scope\n"
            "Description: Define invite success metrics, acceptance criteria, and launch scope.\n"
        ),
    )
    manager.manager.create_skill(
        scope="engineering",
        name="invite-review-checklist",
        content=(
            "# Invite Review Checklist\n"
            "Description: Review invite code for edge cases, test strategy, and regressions.\n"
        ),
    )
    manager.manager.create_skill(
        scope="devops",
        name="invite-deploy-runbook",
        content=(
            "# Invite Deploy Runbook\n"
            "Description: Deploy invite releases with smoke checks, rollback, "
            "and tagged artifacts.\n"
        ),
    )
    return manager


def make_task(target: str, artifact_type: str = "raw_prompt") -> CompanyTask:
    return CompanyTask(
        task_id=f"{target}-skills-1",
        origin="ceo",
        target=target,
        artifact_type=artifact_type,
        payload={
            "original_request": (
                "Build an invite rollout with accessible forms, review coverage, "
                "smoke checks, rollback notes, and launch acceptance criteria."
            ),
            "prd_content": "Invite teammates must support recovery, tests, and rollout.",
        },
        context={},
    )


def test_skill_retrieval_injects_product_reviewer_and_devops_contexts(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    create_company_skills(orchestrator.state)
    builder = ContextBuilder(
        orchestrator.state, orchestrator.company_config.skill_learning
    )

    product_context = builder.build(
        make_task("product"),
        ProductDepartment(memory, FakeAgentLoop()).get_context_requirements(),
    )
    assert any(
        "shared/accessible-rollout-checklist" in item
        for item in product_context["skill_guidance"]
    )
    assert any(
        "product/invite-prd-scope" in item for item in product_context["skill_guidance"]
    )

    engineering_task = make_task("engineering", artifact_type="code")
    engineering_context = builder.build(
        engineering_task,
        EngineeringDepartment(memory, FakeAgentLoop()).get_context_requirements(),
    )
    engineering = EngineeringDepartment(memory, FakeAgentLoop())
    engineering._active_task = engineering_task
    engineering_task.context = engineering_context
    reviewer_prompt = engineering._get_reviewer_system_prompt(engineering_context)
    assert any(
        "shared/accessible-rollout-checklist" in item
        for item in engineering_context["skill_guidance"]
    )
    assert any(
        "engineering/invite-review-checklist" in item
        for item in engineering_context["skill_guidance"]
    )
    assert "Procedural Skills Available" in reviewer_prompt
    assert "engineering/invite-review-checklist" in reviewer_prompt

    devops_context = builder.build(
        make_task("devops", artifact_type="deploy_request"),
        DevOpsDepartment(memory).get_context_requirements(),
    )
    assert any(
        "shared/accessible-rollout-checklist" in item
        for item in devops_context["skill_guidance"]
    )
    assert any(
        "devops/invite-deploy-runbook" in item
        for item in devops_context["skill_guidance"]
    )

    recent = memory.data["skills"]["recently_used"]
    assert {item["role"] for item in recent} >= {"product", "engineering", "devops"}


def test_skill_proposal_approval_flow_creates_approved_skill_and_updates_index(
    tmp_path,
):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    manager = CompanySkillManager(orchestrator.state)
    proposal = SkillProposal(
        proposal_id="skill-devops-rollout-smoke-test",
        action="create",
        scope="devops",
        name="rollout-smoke-test",
        title="Rollout Smoke Test",
        content=(
            "# Rollout Smoke Test\n"
            "Description: Verify deploy health, rollback command, and "
            "customer-visible smoke tests.\n"
        ),
        rationale="Repeated release reviews needed the same smoke-test checklist.",
        source_tasks=["release-1"],
        confidence=0.91,
    )

    proposal_path = manager.create_proposal(proposal)
    pending = manager.list_proposals(status="pending")
    approved = manager.approve_proposal(proposal.proposal_id)
    doc = manager.manager.read_skill("devops", "rollout-smoke-test")
    all_proposals = manager.list_proposals()

    assert proposal_path.exists()
    assert [item.proposal_id for item in pending] == [proposal.proposal_id]
    assert approved.status == "approved"
    assert doc.metadata["approval_status"] == "approved"
    assert doc.metadata["confidence"] == 0.91
    assert all_proposals[0].status == "approved"
    assert memory.data["skill_proposals"][-1]["status"] == "approved"


def test_context_builder_ranks_memories_and_explains_recent_injection(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    memory.update(
        {
            "playbook": {
                "coding_standards": [
                    {
                        "text": "Tune button spacing and visual hierarchy for marketing pages."
                    },
                    {
                        "text": (
                            "Invite rollout code needs rollback notes, smoke checks, "
                            "and pytest coverage."
                        )
                    },
                    {"text": "Database backup jobs should verify retention windows."},
                    {
                        "text": (
                            "Invite acceptance criteria should include recovery "
                            "and regression tests."
                        )
                    },
                    {
                        "text": "Payment reconciliation jobs need daily ledger snapshots."
                    },
                    {
                        "text": (
                            "For invite releases, prefer feature flags and "
                            "accessible form validation."
                        )
                    },
                    {
                        "text": "Mobile animation polish should preserve reduced motion settings."
                    },
                ]
            }
        }
    )
    memory.persist()

    builder = ContextBuilder(
        orchestrator.state, orchestrator.company_config.skill_learning
    )
    context = builder.build(make_task("engineering"), ["playbook.coding_standards"])

    guidance = context["playbook_guidance"]
    assert 1 <= len(guidance) <= 5
    assert any(
        "Invite rollout" in item or "invite releases" in item for item in guidance
    )
    assert all("Payment reconciliation" not in item for item in guidance)
    assert context["playbook_retrieval_explanations"]
    assert "scope_reason=" in context["playbook_retrieval_explanations"][0]

    overview = KnowledgeManager(orchestrator.state).get_overview()
    recent = overview["recently_injected"]
    assert recent[0]["type"] == "playbook"
    assert "scope_reason=" in recent[0]["explanation"]


def test_skill_ranking_explanation_generation_and_prompt_injection(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    manager = create_company_skills(orchestrator.state)
    for idx in range(6):
        manager.manager.create_skill(
            scope="engineering",
            name=f"billing-ledger-{idx}",
            content=(
                f"# Billing Ledger {idx}\n"
                "Description: Reconcile invoices, ledgers, payments, and finance exports.\n"
            ),
        )

    builder = ContextBuilder(
        orchestrator.state, orchestrator.company_config.skill_learning
    )
    task = make_task("engineering", artifact_type="code")
    context = builder.build(
        task, EngineeringDepartment(memory, FakeAgentLoop()).get_context_requirements()
    )

    assert 1 <= len(context["skills"]) <= 5
    assert context["skills"][0]["name"] == "accessible-rollout-checklist"
    assert any(
        skill["name"] == "invite-review-checklist" for skill in context["skills"]
    )
    assert all("billing-ledger" not in item for item in context["skill_guidance"])
    assert all("Why included:" in item for item in context["skill_guidance"])
    assert all("retrieval_explanation" in skill for skill in context["skills"])

    explanations = KnowledgeManager(orchestrator.state).explain_retrieval(
        "invite rollout smoke checks", context["skills"]
    )
    assert explanations
    assert all("scope_reason=" in explanation for explanation in explanations)
    assert any(
        "invite" in explanation or "rollout" in explanation
        for explanation in explanations
    )
