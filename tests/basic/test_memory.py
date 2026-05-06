from pathlib import Path

from aider.memory import ConversationMemory, ProjectMemory, consolidate_conversation


def test_conversation_memory_rolls_to_max_messages():
    memory = ConversationMemory(max_messages=3)
    memory.add(role="user", content="1")
    memory.add(role="assistant", content="2")
    memory.add(role="user", content="3")
    memory.add(role="assistant", content="4")

    assert memory.get() == [
        {"role": "assistant", "content": "2"},
        {"role": "user", "content": "3"},
        {"role": "assistant", "content": "4"},
    ]


def test_project_memory_persists_and_loads(tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    memory = ProjectMemory(str(repo_path))
    memory.update({"facts": {"language": "python"}, "state": "active"})
    memory.persist()

    loaded = ProjectMemory(str(repo_path))
    loaded.load()

    assert loaded.data == {
        "audit_log": [],
        "facts": {"language": "python"},
        "observability": {
            "token_usage_per_department": {},
            "turns_per_phase": {},
        },
        "playbook": {
            "coding_standards": [],
            "deployment_gotchas": [],
            "ux_preferences": [],
        },
        "schema_version": 2,
        "state": "active",
    }


def test_consolidate_conversation_compacts_older_messages(tmp_path: Path):
    repo_path = tmp_path / "repo2"
    repo_path.mkdir()

    conversation = ConversationMemory(max_messages=20)
    for i in range(10):
        role = "user" if i % 2 == 0 else "assistant"
        conversation.add(role=role, content=f"message {i}")

    project = ProjectMemory(str(repo_path))
    consolidate_conversation(conversation, project, keep_recent=4)

    assert len(conversation.get()) == 4
    assert "dreams" in project.data
    assert project.data["dreams"][0]["turns"] == 6


def test_project_memory_migrates_legacy_json(tmp_path: Path):
    repo_path = tmp_path / "repo3"
    memory_dir = repo_path / ".aider"
    memory_dir.mkdir(parents=True)
    (memory_dir / "project_memory.json").write_text(
        '{"audit_log": [], "playbook": {"coding_standards": []}}',
        encoding="utf-8",
    )

    memory = ProjectMemory(str(repo_path))
    data = memory.load()

    assert data["schema_version"] == 2
    assert data["observability"] == {
        "turns_per_phase": {},
        "token_usage_per_department": {},
    }
    assert data["playbook"]["deployment_gotchas"] == []


def test_project_memory_can_use_sqlite_repository(tmp_path: Path):
    from aider.memory import SQLiteMemoryRepository

    repo_path = tmp_path / "repo4"
    repo_path.mkdir()
    repository = SQLiteMemoryRepository(
        repo_path / ".aider" / "project_memory.sqlite", ProjectMemory.DEFAULTS
    )
    memory = ProjectMemory(str(repo_path), repository=repository)
    memory.update({"facts": {"storage": "sqlite"}})
    memory.persist()

    loaded = ProjectMemory(str(repo_path), repository=repository)
    loaded.load()

    assert loaded.data["facts"] == {"storage": "sqlite"}
    assert loaded.data["schema_version"] == 2
