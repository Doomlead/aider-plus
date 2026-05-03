from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Optional, List, Callable

from aider.memory import ProjectMemory, ConversationMemory, Message
from aider.company.schemas import CompanyTask, Deliverable


class Department(ABC):
    name: str = "abstract"

    def __init__(
        self,
        project_memory: ProjectMemory,
        conversation_memory: Optional[ConversationMemory] = None,
    ):
        self.memory = project_memory
        self.conversation = conversation_memory or ConversationMemory()
        self.inbox: asyncio.Queue[CompanyTask] = asyncio.Queue()
        self.tools: List[str] = []
        self._on_deliverable: Optional[Callable[[Deliverable], None]] = None

    async def receive(self, task: CompanyTask) -> None:
        await self.inbox.put(task)

    @abstractmethod
    async def process(self, task: CompanyTask) -> Deliverable:
        ...

    async def run_loop(self) -> None:
        while True:
            task = await self.inbox.get()
            try:
                d = await self.process(task)
                if self._on_deliverable:
                    self._on_deliverable(d)
            except Exception as e:
                if self._on_deliverable:
                    self._on_deliverable(Deliverable(
                        task_id=task.task_id,
                        department=self.name,
                        artifact_type="error",
                        payload=str(e),
                        status="failure",
                    ))
            finally:
                self.inbox.task_done()
