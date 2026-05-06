from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict, List, Optional, Union

from aider.memory import ProjectMemory
from aider.company.department import Department
from aider.company.schemas import (
    ApprovalDecision,
    CompanyEvent,
    CompanyTask,
    Deliverable,
    EventMessage,
)

CompanyMessage = Union[Deliverable, EventMessage]


class CompanyOrchestrator:
    def __init__(self, project_memory: ProjectMemory):
        self.memory = project_memory
        self.departments: Dict[str, Department] = {}
        self._handlers: List[Callable[[CompanyMessage], Awaitable[None]]] = []
        self._gates: Dict[str, asyncio.Future] = {}
        self._pending_tasks: Dict[str, CompanyTask] = {}

    def register(self, dept: Department) -> None:
        self.departments[dept.name] = dept
        dept._on_deliverable = lambda d: asyncio.create_task(self._route(d))

        async def submit_task(task: CompanyTask) -> Optional[Deliverable]:
            return await self.submit(task)

        dept._submit_task = submit_task

    def on_deliverable(self, handler: Callable[[CompanyMessage], Awaitable[None]]):
        self._handlers.append(handler)

    async def _emit(self, message: CompanyMessage) -> None:
        # Stream to Discord / event listeners
        for handler in self._handlers:
            try:
                await handler(message)
            except Exception:
                pass

    async def _route(self, d: Deliverable) -> None:
        await self._emit(d)

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
        elif (
            d.department == "product"
            and next_target == "engineering"
            and d.artifact_type == "memo"
        ):
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
            self._pending_tasks[task.task_id] = task
            await self._emit(self._approval_required_event(task))
            decision = await fut
            self._gates.pop(task.task_id, None)
            self._pending_tasks.pop(task.task_id, None)
            decision = self._normalize_decision(decision)
            if not decision.approved:
                await self._route_rejection(task, decision)
                return None
            task.blocking = False

        await self.departments[task.target].receive(task)
        return None

    def _approval_required_event(self, task: CompanyTask) -> EventMessage:
        metadata = (
            dict(task.context.get("prd_metadata", {}))
            if isinstance(task.context, dict)
            else {}
        )
        prd_content = self._prd_content(task)
        return EventMessage(
            event=CompanyEvent.APPROVAL_REQUIRED,
            task_id=task.task_id,
            payload={
                "task_id": task.task_id,
                "gate_name": metadata.get("gate_name", "prd_approval"),
                "artifact_preview": prd_content[:1500],
                "approver_role": "ceo",
                "project_name": task.context.get("project_name"),
                "handoff_to": task.target,
            },
            metadata={"task": task},
        )

    @staticmethod
    def _prd_content(task: CompanyTask) -> str:
        if isinstance(task.payload, dict):
            return str(
                task.payload.get("prd_content")
                or task.payload.get("previous_prd")
                or task.payload
            )
        return str(task.payload)

    @staticmethod
    def _normalize_decision(decision) -> ApprovalDecision:
        if isinstance(decision, ApprovalDecision):
            return decision
        return ApprovalDecision(approved=bool(decision))

    async def _route_rejection(self, task: CompanyTask, decision: ApprovalDecision) -> None:
        if task.origin not in self.departments:
            return

        revision_count = 1
        previous_metadata = {}
        if isinstance(task.payload, dict):
            previous_metadata = dict(task.payload.get("prd_metadata", {}))
            revision_count = previous_metadata.get("revision_count", 0) + 1

        feedback = decision.metadata.get("feedback") or decision.reason or "Rejected by CEO"
        revision_task = CompanyTask(
            task_id=task.task_id,
            origin="ceo",
            target=task.origin,
            artifact_type="prd",
            payload={
                "previous_prd": self._prd_content(task),
                "ceo_feedback": feedback,
                "revision_count": revision_count,
            },
            blocking=False,
            context={
                **task.context,
                "ceo_feedback": feedback,
                "revision_count": revision_count,
                "approval_action": decision.metadata.get("action", "reject"),
            },
        )
        await self.departments[task.origin].receive(revision_task)

    def approve(self, task_id: str) -> None:
        if task_id in self._gates and not self._gates[task_id].done():
            self._gates[task_id].set_result(ApprovalDecision(approved=True))

    def reject(
        self,
        task_id: str,
        reason: str = "Rejected by CEO",
        metadata: Optional[dict] = None,
    ) -> None:
        if task_id in self._gates and not self._gates[task_id].done():
            self._gates[task_id].set_result(
                ApprovalDecision(approved=False, reason=reason, metadata=metadata or {})
            )

    def request_changes(self, task_id: str, feedback: str) -> None:
        self.reject(task_id, reason=feedback, metadata={"action": "revise", "feedback": feedback})

    async def start(self) -> None:
        await asyncio.gather(
            *[dept.run_loop() for dept in self.departments.values()],
            return_exceptions=True
        )
