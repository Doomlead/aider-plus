from __future__ import annotations

import asyncio
import uuid
from typing import Dict, Optional, List, Callable, Awaitable

from aider.memory import ProjectMemory
from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable


class CompanyOrchestrator:
    def __init__(self, project_memory: ProjectMemory):
        self.memory = project_memory
        self.departments: Dict[str, Department] = {}
        self._handlers: List[Callable[[Deliverable], Awaitable[None]]] = []
        self._gates: Dict[str, asyncio.Future] = {}

    def register(self, dept: Department) -> None:
        self.departments[dept.name] = dept
        dept._on_deliverable = lambda d: asyncio.create_task(self._route(d))

        async def submit_task(task: CompanyTask) -> Optional[Deliverable]:
            return await self.submit(task)

        dept._submit_task = submit_task

    def on_deliverable(self, handler: Callable[[Deliverable], Awaitable[None]]):
        self._handlers.append(handler)

    async def _route(self, d: Deliverable) -> None:
        # Stream to Discord / event listeners
        for handler in self._handlers:
            try:
                await handler(d)
            except Exception:
                pass

        # Auto-route handoffs if specified in metadata. Product PRDs are
        # promoted into structured engineering task context so Engineering
        # receives the generated PRD, not just the original user prompt.
        next_target = d.metadata.get("handoff_to")
        if next_target and next_target in self.departments:
            await self.submit(self._handoff_task(d, next_target))

    def _handoff_task(self, d: Deliverable, next_target: str) -> CompanyTask:
        payload = d.payload
        context = dict(d.metadata.get("context", {}))

        if d.department == "product" and next_target == "engineering" and d.artifact_type == "prd":
            payload = {
                "original_request": d.metadata.get("original_request"),
                "prd_content": d.content,
                "prd_metadata": dict(d.metadata),
            }
            context.update(payload)
        elif d.department == "product" and next_target == "engineering" and d.artifact_type == "memo":
            payload = {
                "clarification_response": d.content,
                "clarification_metadata": dict(d.metadata),
                **context,
            }

        return CompanyTask(
            task_id=d.task_id,
            origin=d.department,
            target=next_target,
            artifact_type=d.metadata.get("next_artifact_type", "general"),
            payload=payload,
            blocking=d.metadata.get("blocking", False),
            context=context,
        )

    async def submit(self, task: CompanyTask) -> Optional[Deliverable]:
        if task.target not in self.departments:
            raise ValueError(f"No department: {task.target}")

        if task.blocking:
            fut = asyncio.get_event_loop().create_future()
            self._gates[task.task_id] = fut
            await self.departments[task.target].receive(task)
            approved = await fut
            del self._gates[task.task_id]
            if not approved:
                return Deliverable(
                    task_id=task.task_id,
                    department=task.target,
                    artifact_type="cancelled",
                    payload="Rejected by CEO",
                    status="failure",
                )
            task.blocking = False

        await self.departments[task.target].receive(task)
        return None

    def approve(self, task_id: str) -> None:
        if task_id in self._gates:
            self._gates[task_id].set_result(True)

    def reject(self, task_id: str) -> None:
        if task_id in self._gates:
            self._gates[task_id].set_result(False)

    async def start(self) -> None:
        await asyncio.gather(
            *[dept.run_loop() for dept in self.departments.values()],
            return_exceptions=True
        )
