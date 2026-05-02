from __future__ import annotations

from typing import List

from .conversation import ConversationMemory, Message
from .project import ProjectMemory


def consolidate_conversation(
    conversation_memory: ConversationMemory,
    project_memory: ProjectMemory,
    *,
    keep_recent: int = 6,
):
    """Compact older conversation turns into lightweight project memory notes."""

    messages: List[Message] = conversation_memory.get()
    if len(messages) <= keep_recent:
        return

    older = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    summary_lines = [f"- {msg.get('role', 'unknown')}: {msg.get('content', '')[:120]}" for msg in older]
    dreams = list(project_memory.data.get("dreams", []))
    dreams.append({"summary": "\n".join(summary_lines), "turns": len(older)})
    project_memory.update({"dreams": dreams})

    conversation_memory.clear()
    conversation_memory.extend(recent)
