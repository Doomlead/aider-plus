from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional

from aider.company.schemas import (
    ApprovalDecision,
    CompanyEvent,
    CompanyTask,
    EventMessage,
)

CompanyEventHandler = Callable[[EventMessage], Awaitable[None]]


class ApprovalManager:
    """Owns human approval requests, pending state, resolution, and recovery."""

    def __init__(self, state_manager, audit_logger: Optional[Callable] = None):
        self.state = state_manager
        self._audit_logger = audit_logger
        self._handlers: List[CompanyEventHandler] = []
        self._gates: Dict[str, asyncio.Future] = {}
        self._pending_tasks: Dict[str, CompanyTask] = {}
        self._recovered_gate_tasks: Dict[str, asyncio.Task] = {}
        self._resolved_task_ids: set[str] = set()

    @property
    def gates(self) -> Dict[str, asyncio.Future]:
        return self._gates

    @property
    def resolved_task_ids(self) -> set[str]:
        return self._resolved_task_ids

    def on_event(self, handler: CompanyEventHandler) -> None:
        self._handlers.append(handler)

    async def _emit(self, event: EventMessage) -> None:
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception:
                pass

    async def create_request(self, task: CompanyTask) -> ApprovalDecision:
        """Create, persist, emit, and await an approval request for a blocking task."""
        event = self.approval_required_event(task)
        fut = asyncio.get_event_loop().create_future()
        self._gates[task.task_id] = fut
        self._pending_tasks[task.task_id] = task
        self.state.add_pending_approval(self.pending_approval_record(task, event))
        self._log(
            "approval_requested",
            self.artifact_preview(task),
            task.origin,
            {"task_id": task.task_id, "gate_name": event.payload.get("gate_name")},
        )
        await self._emit(event)
        return self.normalize_decision(await fut)

    def close_request(self, task_id: str) -> None:
        self._gates.pop(task_id, None)
        self._pending_tasks.pop(task_id, None)
        self.state.remove_pending_approval(task_id)

    async def recover_pending_approvals(
        self,
        on_recovered_decision: Callable[
            [CompanyTask, ApprovalDecision], Awaitable[None]
        ],
    ) -> None:
        """Recreate in-memory approval gates from ProjectMemory and re-emit their UIs."""
        pending_task_ids = {
            str(approval.get("task_id"))
            for approval in self.state.get_pending_approvals()
            if approval.get("status") == "pending" and approval.get("task_id")
        }
        self._resolved_task_ids.difference_update(pending_task_ids)
        for approval in self.state.get_pending_approvals():
            if approval.get("status") != "pending":
                continue
            task = self.task_from_pending_approval(approval)
            if task is None or task.task_id in self._gates:
                continue
            self._gates[task.task_id] = asyncio.get_event_loop().create_future()
            self._pending_tasks[task.task_id] = task
            await self._emit(self.approval_required_event(task))
            self._recovered_gate_tasks[task.task_id] = asyncio.create_task(
                self._complete_recovered_gate(task, on_recovered_decision)
            )

    async def _complete_recovered_gate(
        self,
        task: CompanyTask,
        on_recovered_decision: Callable[
            [CompanyTask, ApprovalDecision], Awaitable[None]
        ],
    ) -> None:
        try:
            decision = self.normalize_decision(await self._gates[task.task_id])
            self.close_request(task.task_id)
            await on_recovered_decision(task, decision)
        finally:
            self._recovered_gate_tasks.pop(task.task_id, None)

    async def handle_approval_response(
        self,
        task_id: str,
        approved: bool,
        source: str = "discord",
        reason: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Resolve an approval gate once, ignoring duplicate approval UIs."""
        if task_id in self._resolved_task_ids:
            return False
        if task_id not in self._gates or self._gates[task_id].done():
            self._resolved_task_ids.add(task_id)
            return False

        self._resolved_task_ids.add(task_id)
        response_metadata = {"approved_by": source}
        if metadata:
            response_metadata.update(metadata)
        if approved:
            self.approve(task_id, metadata=response_metadata)
        else:
            self.reject(
                task_id, reason=reason or "Rejected by CEO", metadata=response_metadata
            )
        return True

    def approve(self, task_id: str, metadata: Optional[dict] = None) -> None:
        if task_id in self._gates and not self._gates[task_id].done():
            self._log_approval_resolved(task_id, True, None, metadata)
            self._gates[task_id].set_result(
                ApprovalDecision(approved=True, metadata=metadata or {})
            )

    def reject(
        self,
        task_id: str,
        reason: str = "Rejected by CEO",
        metadata: Optional[dict] = None,
    ) -> None:
        if task_id in self._gates and not self._gates[task_id].done():
            self._log_approval_resolved(task_id, False, reason, metadata)
            self._gates[task_id].set_result(
                ApprovalDecision(approved=False, reason=reason, metadata=metadata or {})
            )

    def request_changes(self, task_id: str, feedback: str) -> None:
        self.reject(
            task_id,
            reason=feedback,
            metadata={"action": "revise", "feedback": feedback},
        )

    def approval_required_event(self, task: CompanyTask) -> EventMessage:
        context = task.context if isinstance(task.context, dict) else {}
        metadata = dict(context.get("prd_metadata", {}))
        gate_name = context.get("gate_name") or metadata.get(
            "gate_name", "prd_approval"
        )
        artifact_preview = context.get("artifact_preview") or self.artifact_preview(
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

    def pending_approval_record(
        self, task: CompanyTask, event: Optional[EventMessage] = None
    ) -> dict:
        event = event or self.approval_required_event(task)
        return {
            "task_id": task.task_id,
            "gate_name": event.payload.get("gate_name"),
            "department": task.origin,
            "artifact_preview": event.payload.get("artifact_preview", ""),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "pending",
            "task": self.serialize_task(task),
        }

    def task_from_pending_approval(self, approval: dict) -> Optional[CompanyTask]:
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
        return CompanyTask(
            task_id=str(task_id),
            origin=str(approval.get("department") or "product"),
            target="engineering",
            artifact_type="test_report" if gate_name == "release_approval" else "prd",
            payload=approval.get("artifact_preview", ""),
            blocking=True,
            context={
                "gate_name": gate_name,
                "artifact_preview": approval.get("artifact_preview", ""),
            },
        )

    def serialize_task(self, task: CompanyTask) -> dict:
        return {
            "task_id": task.task_id,
            "origin": task.origin,
            "target": task.target,
            "artifact_type": task.artifact_type,
            "payload": self._json_safe(task.payload),
            "blocking": task.blocking,
            "context": self._json_safe(task.context),
        }

    @staticmethod
    def normalize_decision(decision) -> ApprovalDecision:
        if isinstance(decision, ApprovalDecision):
            return decision
        return ApprovalDecision(approved=bool(decision))

    @staticmethod
    def artifact_preview(task: CompanyTask) -> str:
        if isinstance(task.payload, dict):
            return str(
                task.payload.get("prd_content")
                or task.payload.get("previous_prd")
                or task.payload.get("qa_report")
                or task.payload.get("engineering_result")
                or task.payload
            )
        return str(task.payload)

    def _log_approval_resolved(
        self,
        task_id: str,
        approved: bool,
        reason: Optional[str],
        metadata: Optional[dict],
    ) -> None:
        task = self._pending_tasks.get(task_id)
        payload = {"task_id": task_id, "approved": approved, "reason": reason}
        department = task.origin if task else "orchestrator"
        event_metadata = {"task_id": task_id, "approved": approved}
        if reason:
            event_metadata["reason"] = reason
        if metadata:
            event_metadata.update(metadata)
            if metadata.get("feedback"):
                event_metadata["feedback"] = metadata.get("feedback")
        self._log("approval_resolved", payload, department, event_metadata)

    def _log(
        self, event_type: str, payload, department: str, metadata: Optional[dict]
    ) -> None:
        if self._audit_logger is None:
            return
        try:
            self._audit_logger(event_type, payload, department, metadata)
        except Exception:
            pass

    @classmethod
    def _json_safe(cls, value):
        if is_dataclass(value):
            return cls._json_safe(asdict(value))
        if isinstance(value, dict):
            return {str(k): cls._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
