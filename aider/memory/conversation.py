from __future__ import annotations

from collections import deque
from typing import Deque, List, TypedDict


class Message(TypedDict):
    role: str
    content: str


class ConversationMemory:
    """Rolling in-memory buffer of recent conversation exchanges."""

    def __init__(self, max_messages: int = 20):
        self.max_messages = max(2, max_messages)
        self._buffer: Deque[Message] = deque(maxlen=self.max_messages)

    def add(self, *, role: str, content: str):
        self._buffer.append(Message(role=role, content=content))

    def extend(self, messages: List[Message]):
        for msg in messages:
            self.add(role=msg.get("role", "unknown"), content=msg.get("content", ""))

    def get(self) -> List[Message]:
        return list(self._buffer)

    def clear(self):
        self._buffer.clear()
