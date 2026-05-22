from aider.company.template_selector import select_file_generation_policy, select_template
from aider.memory import MemoryRecord, MemoryStore, ProjectMemory


def test_select_template_prefers_memory_evidence(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    winning = store.append_record(
        MemoryRecord(
            content="Build a stripe webhook API with retries and signing",
            scope="project",
            usage_count=4,
            successful_uses=3,
            acceptance_rate=0.75,
            metadata={"template_key": "fastapi-api"},
        )
    )
    store.append_record(
        MemoryRecord(
            content="Build a stripe webhook API",
            scope="project",
            usage_count=1,
            successful_uses=0,
            acceptance_rate=0.0,
            metadata={"template_key": "nextjs-saas"},
        )
    )

    decision = select_template(
        idea="Create webhook service for billing retries",
        project_name="Billing Hooks",
        role_context="engineering",
        memory_store=store,
    )

    assert decision.template_key == "fastapi-api"
    assert decision.confidence > 0.6
    assert winning.id in decision.memory_record_ids
    assert decision.reasons


def test_select_template_falls_back_to_custom_without_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore(ProjectMemory(str(tmp_path)))

    decision = select_template(
        idea="Build a novel internal workflow tool",
        project_name=None,
        role_context=None,
        memory_store=store,
    )

    assert decision.template_key == "custom"
    assert decision.confidence <= 0.6
    assert decision.memory_record_ids == []


def test_select_template_applies_correction_and_preference_penalties(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    store.append_record(
        MemoryRecord(
            content="Build dashboard for analytics",
            scope="project",
            usage_count=5,
            successful_uses=4,
            acceptance_rate=0.8,
            reinforcement_score=0.8,
            metadata={
                "template_key": "data-dashboard",
                "rewrite_count": 4,
                "template_preferences": {"data-dashboard": "avoid"},
            },
        )
    )
    winner = store.append_record(
        MemoryRecord(
            content="Build dashboard for analytics",
            scope="project",
            usage_count=2,
            successful_uses=2,
            acceptance_rate=1.0,
            reinforcement_score=1.0,
            metadata={"template_key": "streamlit-dashboard"},
        )
    )

    decision = select_template(
        idea="Build analytics dashboard",
        project_name="KPI Board",
        role_context="product",
        memory_store=store,
    )

    assert decision.template_key == "streamlit-dashboard"
    assert winner.id in decision.memory_record_ids


def test_select_template_demotes_when_repeated_mismatches_present(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    for _ in range(2):
        store.append_record(
            MemoryRecord(
                content="Build internal admin workflow with approvals",
                scope="project",
                usage_count=4,
                successful_uses=3,
                acceptance_rate=0.75,
                reinforcement_score=0.5,
                metadata={
                    "template_key": "internal-admin",
                    "rewrite_count": 3,
                },
            )
        )

    decision = select_template(
        idea="Build internal admin workflow with approvals",
        project_name="Ops Workbench",
        role_context="product",
        memory_store=store,
    )

    assert decision.template_key == "custom"
    assert any("repeated mismatch evidence" in reason for reason in decision.reasons)


def test_select_file_generation_policy_falls_back_to_neutral_when_uncertain(tmp_path):
    from aider.memory import ProjectMemory
    from aider.memory.store import MemoryStore

    store = MemoryStore(ProjectMemory(str(tmp_path)))
    policy = select_file_generation_policy(
        file_path="src/worker/tasks.py",
        request_text="create job",
        memory_store=store,
    )
    assert policy["strategy"] == "neutral_todo_boundaries"
    assert policy["intent"] in {"etl_job", "generic_file", "api_handler", "cli_command", "ui_component"}
