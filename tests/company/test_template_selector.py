from aider.company.template_selector import select_template
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
