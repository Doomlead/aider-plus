from __future__ import annotations

import asyncio
from functools import wraps
from abc import ABC, abstractmethod
from typing import Awaitable, Optional, List, Callable

from aider.company.audit import append_audit_event
from aider.company.config import DepartmentConfig
from aider.company.interfaces import Deliverable
from aider.memory import ProjectMemory, ConversationMemory
from aider.memory import communication as communication_memory
from aider.company.schemas import CompanyEvent, CompanyTask, EventMessage


class Department(ABC):
    name: str = "abstract"
    allowed_tools: List[str] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        process = cls.__dict__.get("process")
        if process is None or getattr(
            process, "_communication_recording_wrapped", False
        ):
            return

        @wraps(process)
        async def _recording_process(self, task: CompanyTask) -> Deliverable:
            communication_memory.task_received(self.memory, task, department=self.name)
            try:
                deliverable = await process(self, task)
            except Exception as exc:
                communication_memory.failure(
                    self.memory, exc, task=task, department=self.name, stage="process"
                )
                raise
            communication_memory.deliverable_produced(self.memory, deliverable)
            return deliverable

        _recording_process._communication_recording_wrapped = True
        cls.process = _recording_process

    def __init__(
        self,
        project_memory: ProjectMemory,
        conversation_memory: Optional[ConversationMemory] = None,
        config: Optional[DepartmentConfig] = None,
    ):
        self.memory = project_memory
        self.conversation = conversation_memory or ConversationMemory()
        self.config = config or DepartmentConfig(name=self.name)
        self.agent_config = self.config
        self.inbox: asyncio.Queue[CompanyTask] = asyncio.Queue()
        self.tools: List[str] = []
        self._on_deliverable: Optional[Callable[[Deliverable], None]] = None
        self._on_event: Optional[Callable[[EventMessage], Awaitable[None]]] = None
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

    def get_context_requirements(self) -> list[str]:
        return ["playbook.*", "skills.shared", f"skills.{self.name}"]

    def _get_caching_enabled(self) -> bool:
        return bool(self.agent_config.enable_caching)

    async def receive(self, task: CompanyTask) -> None:
        await self.inbox.put(task)

    async def _emit_lifecycle_event(
        self, task_id: str, event_name: str, payload: Optional[dict] = None
    ) -> None:
        """Emit a lifecycle event through company listeners and the audit log."""
        event_payload = dict(payload or {})
        self._log_event(event_name, event_payload, {"task_id": task_id})
        if self._on_event is None:
            return
        message = EventMessage(
            event=CompanyEvent.LIFECYCLE,
            task_id=task_id,
            payload={"name": event_name, **event_payload},
            metadata={"department": self.name},
        )
        emitted = self._on_event(message)
        if hasattr(emitted, "__await__"):
            await emitted

    @abstractmethod
    async def process(self, task: CompanyTask) -> Deliverable: ...

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
                communication_memory.failure(
                    self.memory, e, task=task, department=self.name, stage="run_loop"
                )
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
