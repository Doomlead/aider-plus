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

    assert loaded.data == {"facts": {"language": "python"}, "state": "active"}


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
