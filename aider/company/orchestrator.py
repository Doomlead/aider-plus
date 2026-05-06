from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from aider.memory import ProjectMemory
from aider.company.department import Department
from aider.company.project import Project
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
        self.active_project: Optional[Project] = None
        self._recovered_gate_tasks: Dict[str, asyncio.Task] = {}

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

        if await self._route_project_state(d):
            return

        # Auto-route handoffs if specified in metadata. Product PRDs are
        # promoted into structured engineering task context so Engineering
        # receives the generated PRD, not just the original user prompt.
        next_target = d.metadata.get("handoff_to")
        if next_target and next_target in self.departments:
            await self.submit(self._handoff_task(d, next_target))

    async def _route_project_state(self, d: Deliverable) -> bool:
        project = self.active_project
        if project is None:
            return False

        if project.phase == "prototyping" and d.department == "product":
            if d.artifact_type == "prd" and d.status == "success":
                project.prd = str(d.content)
                next_target = d.metadata.get("handoff_to")
                if next_target and next_target in self.departments:
                    await self.submit(self._handoff_task(d, next_target))
                    return True
            return False

        if project.phase == "development" and d.department == "engineering":
            project.engineering_result = d
            if d.status == "success":
                project.phase = "qa"
                if "qa" in self.departments:
                    await self.submit(self._qa_task(d))
                return True

            if d.status == "failure" and "engineering" in self.departments:
                await self.submit(self._engineering_revision_task(d))
                return True

        if project.phase == "qa" and d.department == "qa":
            project.qa_result = d
            project.phase = "release_ready"
            await self._request_release_approval(d)
            return True

        return False

    def _handoff_task(self, d: Deliverable, next_target: str) -> CompanyTask:
        payload = d.payload
        context = dict(d.metadata.get("context", {}))

        if (
            d.department == "product"
            and next_target == "engineering"
            and d.artifact_type == "prd"
        ):
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
            await self._open_approval_gate(task)
            decision = await self._gates[task.task_id]
            self._close_approval_gate(task.task_id)
            decision = self._normalize_decision(decision)
            if not decision.approved:
                await self._route_rejection(task, decision)
                return None
            self._advance_after_approval(task)
            task.blocking = False

        await self.departments[task.target].receive(task)
        return None

    async def recover_pending_approvals(self) -> None:
        """Recreate in-memory approval gates from ProjectMemory and re-emit their UIs."""
        for approval in self._stored_pending_approvals():
            if approval.get("status") != "pending":
                continue
            task = self._task_from_pending_approval(approval)
            if task is None or task.task_id in self._gates:
                continue
            fut = asyncio.get_event_loop().create_future()
            self._gates[task.task_id] = fut
            self._pending_tasks[task.task_id] = task
            await self._emit(self._approval_required_event(task))
            self._recovered_gate_tasks[task.task_id] = asyncio.create_task(
                self._complete_recovered_gate(task)
            )

    async def _complete_recovered_gate(self, task: CompanyTask) -> None:
        try:
            decision = self._normalize_decision(await self._gates[task.task_id])
            self._close_approval_gate(task.task_id)
            if not decision.approved:
                if self._is_release_approval(task):
                    if self.active_project:
                        self.active_project.phase = "development"
                    await self._route_release_rejection(task, decision)
                else:
                    await self._route_rejection(task, decision)
                return
            if self._is_release_approval(task):
                if self.active_project:
                    self.active_project.phase = "done"
                return
            self._advance_after_approval(task)
            task.blocking = False
            await self.departments[task.target].receive(task)
        finally:
            self._recovered_gate_tasks.pop(task.task_id, None)

    async def _open_approval_gate(self, task: CompanyTask) -> None:
        fut = asyncio.get_event_loop().create_future()
        self._gates[task.task_id] = fut
        self._pending_tasks[task.task_id] = task
        self._persist_pending_approval(task)
        await self._emit(self._approval_required_event(task))

    def _close_approval_gate(self, task_id: str) -> None:
        self._gates.pop(task_id, None)
        self._pending_tasks.pop(task_id, None)
        self._remove_pending_approval(task_id)

    def _approval_required_event(self, task: CompanyTask) -> EventMessage:
        context = task.context if isinstance(task.context, dict) else {}
        metadata = dict(context.get("prd_metadata", {}))
        gate_name = context.get("gate_name") or metadata.get(
            "gate_name", "prd_approval"
        )
        artifact_preview = context.get("artifact_preview") or self._artifact_preview(
            task
        )
        return EventMessage(
            event=CompanyEvent.APPROVAL_REQUIRED,
            task_id=task.task_id,
            payload={
                "task_id": task.task_id,
                "gate_name": gate_name,
                "artifact_preview": str(artifact_preview)[:1500],
                "approver_role": context.get("approver_role", "ceo"),
                "project_name": context.get("project_name"),
                "handoff_to": context.get("handoff_to", task.target),
            },
            metadata={"task": task},
        )

    @staticmethod
    def _artifact_preview(task: CompanyTask) -> str:
        if isinstance(task.payload, dict):
            return str(
                task.payload.get("prd_content")
                or task.payload.get("previous_prd")
                or task.payload.get("qa_report")
                or task.payload.get("engineering_result")
                or task.payload
            )
        return str(task.payload)

    @staticmethod
    def _prd_content(task: CompanyTask) -> str:
        if isinstance(task.payload, dict):
            return str(
                task.payload.get("prd_content")
                or task.payload.get("previous_prd")
                or task.payload
            )
        return str(task.payload)

    def _qa_task(self, d: Deliverable) -> CompanyTask:
        context = dict(d.metadata.get("context", {}))
        if self.active_project:
            context.setdefault("project_name", self.active_project.name)
        return CompanyTask(
            task_id=d.task_id,
            origin="engineering",
            target="qa",
            artifact_type="code",
            payload={
                "engineering_result": d.content,
                "engineering_metadata": dict(d.metadata),
                "prd_content": self.active_project.prd if self.active_project else "",
            },
            blocking=False,
            context=context,
        )

    def _engineering_revision_task(self, d: Deliverable) -> CompanyTask:
        context = dict(d.metadata.get("context", {}))
        context["engineering_failure"] = d.content
        return CompanyTask(
            task_id=d.task_id,
            origin="orchestrator",
            target="engineering",
            artifact_type="raw_prompt",
            payload={
                "previous_engineering_result": d.content,
                "failure_metadata": dict(d.metadata),
                "instruction": "Address the engineering failure and resubmit the implementation.",
            },
            blocking=False,
            context=context,
        )

    async def _request_release_approval(self, d: Deliverable) -> None:
        task = CompanyTask(
            task_id=d.task_id,
            origin="qa",
            target="engineering",
            artifact_type="test_report",
            payload={
                "qa_report": d.content,
                "qa_metadata": dict(d.metadata),
            },
            blocking=True,
            context={
                **dict(d.metadata.get("context", {})),
                "gate_name": "release_approval",
                "artifact_preview": d.content,
                "handoff_to": "release",
            },
        )
        await self._open_approval_gate(task)
        decision = self._normalize_decision(await self._gates[task.task_id])
        self._close_approval_gate(task.task_id)

        if decision.approved:
            if self.active_project:
                self.active_project.phase = "done"
            return

        if self.active_project:
            self.active_project.phase = "development"
        await self._route_release_rejection(task, decision)

    def _persist_pending_approval(self, task: CompanyTask) -> None:
        approval = self._pending_approval_record(task)
        approvals = [
            item
            for item in self._stored_pending_approvals()
            if item.get("task_id") != task.task_id
        ]
        approvals.append(approval)
        self.memory.update({"pending_approvals": approvals})
        self.memory.persist()

    def _remove_pending_approval(self, task_id: str) -> None:
        approvals = [
            item
            for item in self._stored_pending_approvals()
            if item.get("task_id") != task_id
        ]
        self.memory.update({"pending_approvals": approvals})
        self.memory.persist()

    def _pending_approval_record(self, task: CompanyTask) -> dict:
        event = self._approval_required_event(task)
        return {
            "task_id": task.task_id,
            "gate_name": event.payload.get("gate_name"),
            "department": task.origin,
            "artifact_preview": event.payload.get("artifact_preview", ""),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "pending",
            "task": self._serialize_task(task),
        }

    def _stored_pending_approvals(self) -> List[dict]:
        approvals = self.memory.data.get("pending_approvals", [])
        if not isinstance(approvals, list):
            return []
        return [item for item in approvals if isinstance(item, dict)]

    def _task_from_pending_approval(self, approval: dict) -> Optional[CompanyTask]:
        task_data = approval.get("task")
        if isinstance(task_data, dict):
            return CompanyTask(
                task_id=str(task_data.get("task_id") or approval.get("task_id")),
                origin=str(
                    task_data.get("origin") or approval.get("department") or "ceo"
                ),
                target=str(task_data.get("target") or "engineering"),
                artifact_type=task_data.get("artifact_type", "general"),
                payload=task_data.get("payload", approval.get("artifact_preview", "")),
                blocking=True,
                context=(
                    task_data.get("context", {})
                    if isinstance(task_data.get("context"), dict)
                    else {}
                ),
            )

        task_id = approval.get("task_id")
        gate_name = approval.get("gate_name", "prd_approval")
        if not task_id:
            return None
        target = "engineering"
        origin = approval.get("department") or "product"
        artifact_type = "test_report" if gate_name == "release_approval" else "prd"
        context = {
            "gate_name": gate_name,
            "artifact_preview": approval.get("artifact_preview", ""),
        }
        return CompanyTask(
            task_id=str(task_id),
            origin=str(origin),
            target=target,
            artifact_type=artifact_type,
            payload=approval.get("artifact_preview", ""),
            blocking=True,
            context=context,
        )

    def _serialize_task(self, task: CompanyTask) -> dict:
        return {
            "task_id": task.task_id,
            "origin": task.origin,
            "target": task.target,
            "artifact_type": task.artifact_type,
            "payload": self._json_safe(task.payload),
            "blocking": task.blocking,
            "context": self._json_safe(task.context),
        }

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if is_dataclass(value):
            return cls._json_safe(asdict(value))
        if isinstance(value, dict):
            return {str(k): cls._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _is_release_approval(task: CompanyTask) -> bool:
        context = task.context if isinstance(task.context, dict) else {}
        return context.get("gate_name") == "release_approval"

    @staticmethod
    def _normalize_decision(decision) -> ApprovalDecision:
        if isinstance(decision, ApprovalDecision):
            return decision
        return ApprovalDecision(approved=bool(decision))

    def _advance_after_approval(self, task: CompanyTask) -> None:
        project = self.active_project
        if project is None:
            return
        if (
            project.phase == "prototyping"
            and task.origin == "product"
            and task.target == "engineering"
            and task.artifact_type == "prd"
        ):
            project.prd = self._prd_content(task)
            project.phase = "development"

    async def _route_rejection(
        self, task: CompanyTask, decision: ApprovalDecision
    ) -> None:
        if task.origin not in self.departments:
            return

        revision_count = 1
        previous_metadata = {}
        if isinstance(task.payload, dict):
            previous_metadata = dict(task.payload.get("prd_metadata", {}))
            revision_count = previous_metadata.get("revision_count", 0) + 1

        if self.active_project and task.origin == "product":
            self.active_project.phase = "prototyping"

        feedback = (
            decision.metadata.get("feedback") or decision.reason or "Rejected by CEO"
        )
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
        if self.active_project and task.origin == "product":
            self.active_project.revision_count = revision_count
        await self.departments[task.origin].receive(revision_task)

    async def _route_release_rejection(
        self, task: CompanyTask, decision: ApprovalDecision
    ) -> None:
        if "engineering" not in self.departments:
            return
        feedback = (
            decision.metadata.get("feedback") or decision.reason or "Rejected by CEO"
        )
        await self.departments["engineering"].receive(
            CompanyTask(
                task_id=task.task_id,
                origin="ceo",
                target="engineering",
                artifact_type="test_report",
                payload={
                    "qa_report": (
                        task.payload.get("qa_report")
                        if isinstance(task.payload, dict)
                        else task.payload
                    ),
                    "ceo_feedback": feedback,
                },
                blocking=False,
                context={**task.context, "ceo_feedback": feedback},
            )
        )

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
        self.reject(
            task_id,
            reason=feedback,
            metadata={"action": "revise", "feedback": feedback},
        )

    async def start(self) -> None:
        await asyncio.gather(
            *[dept.run_loop() for dept in self.departments.values()],
            return_exceptions=True,
        )
