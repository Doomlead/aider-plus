from __future__ import annotations

import json

from aider.company.knowledge import KnowledgeManager
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.skills import CompanySkillManager, SkillProposal
from aider.memory import ProjectMemory


def test_knowledge_overview_and_search_include_all_surfaces(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    state = orchestrator.state
    memory.update(
        {
            "playbook": {
                "deployment_gotchas": [
                    {
                        "content": "Use the rollout checklist before production deploys.",
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ]
            }
        }
    )
    memory.persist()

    skill_manager = CompanySkillManager(state)
    skill_manager.manager.create_skill(
        scope="shared",
        name="rollout-checklist",
        content="# Rollout Checklist\nDescription: Verify flags, migrations, and rollback plans.\n",
    )
    skill_manager.record_skill_usage(
        [skill_manager.manager.list_skills(scopes=["shared"])[0]], role="coo"
    )
    proposal = SkillProposal(
        proposal_id="skill-shared-release-notes",
        action="create",
        scope="shared",
        name="release-notes",
        title="Release Notes",
        content="# Release Notes\nDescription: Draft concise launch notes.\n",
        rationale="Repeated release-note drafting succeeded.",
        confidence=0.75,
    )
    skill_manager.create_proposal(proposal)

    coo_dir = tmp_path / ".aider" / "coo"
    coo_dir.mkdir(parents=True)
    (coo_dir / "memory.jsonl").write_text(
        json.dumps(
            {
                "created_at": "2026-01-02T00:00:00Z",
                "type": "ceo_preference",
                "content": "CEO prefers rollout summaries with risk notes.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manager = KnowledgeManager(state)
    overview = manager.get_overview(query="rollout")

    assert overview["counts"]["playbooks"] == 1
    assert overview["counts"]["skills"] == 1
    assert overview["counts"]["pending_proposals"] == 1
    assert overview["counts"]["coo_memory_entries"] == 1
    assert overview["recent_skills"][0]["name"] == "rollout-checklist"
    assert (
        overview["pending_proposals"][0]["proposal_id"] == "skill-shared-release-notes"
    )
    assert {item["type"] for item in overview["search_results"]} >= {
        "playbook",
        "skill",
        "coo_memory",
    }
