from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Awaitable, Optional, List, Callable

from aider.company.audit import append_audit_event
from aider.memory import ProjectMemory, ConversationMemory, Message
from aider.company.schemas import CompanyTask, Deliverable


class Department(ABC):
    name: str = "abstract"
    allowed_tools: List[str] = []

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
        self._submit_task: Optional[
            Callable[[CompanyTask], Awaitable[Optional[Deliverable]]]
        ] = None

    def can_use_tool(self, tool_name: str) -> bool:
        allowed = not self.allowed_tools or tool_name in self.allowed_tools
        if not allowed:
            self._log_event(
                "tool_permission_violation",
                {"tool": tool_name, "allowed_tools": self.allowed_tools},
            )
        return allowed

    def build_context(self, task: CompanyTask) -> dict:
        context = dict(task.context or {})
        playbook = self.memory.data.get("playbook", {})
        if not isinstance(playbook, dict):
            playbook = {}

        relevant = {}
        if self.name == "engineering":
            relevant["coding_standards"] = list(playbook.get("coding_standards") or [])
            if playbook.get("ux_preferences"):
                relevant["ux_preferences"] = list(playbook.get("ux_preferences") or [])
        elif self.name == "devops":
            relevant["deployment_gotchas"] = list(
                playbook.get("deployment_gotchas") or []
            )
        else:
            relevant = {
                key: list(value or [])
                for key, value in playbook.items()
                if isinstance(value, list) and value
            }

        if relevant:
            context["playbook"] = relevant
            context["playbook_guidance"] = self._format_playbook_guidance(relevant)
        return context

    async def receive(self, task: CompanyTask) -> None:
        task.context = self.build_context(task)
        await self.inbox.put(task)

    @abstractmethod
    async def process(self, task: CompanyTask) -> Deliverable: ...

    @staticmethod
    def _format_playbook_guidance(playbook: dict) -> list[str]:
        guidance = []
        for entries in playbook.values():
            for entry in entries:
                guidance.append(str(entry))
        return guidance

    def _log_event(
        self, event_type: str, payload, metadata: Optional[dict] = None
    ) -> None:
        try:
            memory_data = getattr(self.memory, "data", {})
            project_id = str(
                memory_data.get("project_id") or getattr(self.memory, "repo_path", "")
            )
            append_audit_event(
                self.memory,
                project_id=project_id,
                department=self.name,
                event_type=event_type,
                payload=payload,
                metadata=metadata,
            )
        except Exception:
            pass

    async def run_loop(self) -> None:
        while True:
            task = await self.inbox.get()
            try:
                d = await self.process(task)
                self._log_event(
                    "deliverable_produced",
                    d.payload,
                    {
                        "task_id": d.task_id,
                        "status": d.status,
                        "artifact_type": d.artifact_type,
                    },
                )
                if self._on_deliverable:
                    self._on_deliverable(d)
            except Exception as e:
                if self._on_deliverable:
                    self._on_deliverable(
                        Deliverable(
                            task_id=task.task_id,
                            department=self.name,
                            artifact_type="error",
                            payload=str(e),
                            status="failure",
                        )
                    )
            finally:
                self.inbox.task_done()
