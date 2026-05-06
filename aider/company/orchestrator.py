from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from aider.memory import ProjectMemory
from aider.company.context import ContextBuilder
from aider.company.department import Department
from aider.company.project import Project
from aider.company.state import CompanyStateManager
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
        self.state = CompanyStateManager(project_memory)
        self.context_builder = ContextBuilder(self.state)
        self.departments: Dict[str, Department] = {}
        self._handlers: List[Callable[[CompanyMessage], Awaitable[None]]] = []
        self._gates: Dict[str, asyncio.Future] = {}
        self._pending_tasks: Dict[str, CompanyTask] = {}
        self._recovered_gate_tasks: Dict[str, asyncio.Task] = {}
        self._resolved_task_ids: set[str] = set()

    @property
    def active_project(self) -> Optional[Project]:
        return self.state.active_project

    @active_project.setter
    def active_project(self, project: Optional[Project]) -> None:
        self.state.active_project = project

    @property
    def memory(self) -> ProjectMemory:
        return self.state.memory

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
        self._log_event(
            "deliverable_produced",
            d.payload,
            d.department,
            {
                "task_id": d.task_id,
                "status": d.status,
                "artifact_type": d.artifact_type,
            },
        )
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
                project.requires_design = bool(d.metadata.get("requires_design", False))
                next_target = (
                    "ux" if project.requires_design else d.metadata.get("handoff_to")
                )
                if next_target == "ux" and "ux" not in self.departments:
                    next_target = "engineering"
                if next_target and next_target in self.departments:
                    await self.submit(self._handoff_task(d, next_target))
                    return True
            return False

        if project.phase == "design" and d.department == "ux":
            project.design_spec = (
                d.content if isinstance(d.content, dict) else {"content": d.content}
            )
            if d.status == "success":
                self.state.set_current_phase("development")
                if "engineering" in self.departments:
                    await self.submit(self._handoff_task(d, "engineering"))
                return True

        if project.phase == "development" and d.department == "engineering":
            project.engineering_result = d
            if d.status == "success":
                self.state.set_current_phase("qa")
                if "qa" in self.departments:
                    await self.submit(self._qa_task(d))
                return True

            if d.status == "failure" and "engineering" in self.departments:
                await self.submit(self._engineering_revision_task(d))
                return True

        if project.phase == "qa" and d.department == "qa":
            project.qa_result = d
            self._log_event(
                "qa_pass" if d.status == "success" else "qa_fail",
                d.payload,
                d.department,
                {"task_id": d.task_id, "status": d.status},
            )
            self.state.set_current_phase("release_ready")
            await self._request_release_approval(d)
            return True

        if project.phase == "deploying" and d.department == "devops":
            project.deploy_result = d
            self._log_event(
                "deployment_success" if d.status == "success" else "deployment_failure",
                d.payload,
                d.department,
                {"task_id": d.task_id, "status": d.status},
            )
            self.state.set_current_phase("post_mortem")
            await self._run_post_mortem(d)
            if d.status == "success":
                self.state.set_current_phase("done")
                return True

            self.state.set_current_phase("development")
            if "engineering" in self.departments:
                await self.submit(self._engineering_infra_revision_task(d))
            return True

        return False

    def _handoff_task(self, d: Deliverable, next_target: str) -> CompanyTask:
        payload = d.payload
        context = dict(d.metadata.get("context", {}))

        if (
            d.department == "product"
            and d.artifact_type == "prd"
            and next_target
            in {
                "engineering",
                "ux",
            }
        ):
            payload = {
                "original_request": d.metadata.get("original_request"),
                "prd_content": d.content,
                "prd_metadata": dict(d.metadata),
            }
            context.update(payload)
        elif d.department == "ux" and next_target == "engineering":
            prd_content = context.get("prd_content")
            payload = {
                "original_request": context.get("original_request"),
                "prd_content": prd_content,
                "prd_metadata": dict(context.get("prd_metadata", {})),
                "design_spec": d.content,
                "design_metadata": dict(d.metadata),
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

    async def _dispatch(self, task: CompanyTask) -> None:
        department = self.departments[task.target]
        task.context = self.context_builder.build(
            task,
            department.get_context_requirements(),
            self.active_project,
        )
        await department.receive(task)

    async def submit(self, task: CompanyTask) -> Optional[Deliverable]:
        self._log_event(
            "task_submitted",
            task.payload,
            task.target,
            {
                "task_id": task.task_id,
                "origin": task.origin,
                "artifact_type": task.artifact_type,
            },
        )
        if task.target not in self.departments:
            raise ValueError(f"No department: {task.target}")

        if task.blocking:
            await self._open_approval_gate(task)
            decision = await self._gates[task.task_id]
            self._close_approval_gate(task.task_id)
            decision = self._normalize_decision(decision)
            if not decision.approved:
                if self._is_release_approval(task):
                    if self.active_project:
                        self.state.set_current_phase("development")
                    await self._route_release_rejection(task, decision)
                else:
                    await self._route_rejection(task, decision)
                return None
            if self._is_release_approval(task):
                await self._submit_devops_after_release(task)
                return None
            self._advance_after_approval(task)
            task.blocking = False

        await self._dispatch(task)
        return None

    async def recover_pending_approvals(self) -> None:
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
                        self.state.set_current_phase("development")
                    await self._route_release_rejection(task, decision)
                else:
                    await self._route_rejection(task, decision)
                return
            if self._is_release_approval(task):
                await self._submit_devops_after_release(task)
                return
            self._advance_after_approval(task)
            task.blocking = False
            await self._dispatch(task)
        finally:
            self._recovered_gate_tasks.pop(task.task_id, None)

    async def _open_approval_gate(self, task: CompanyTask) -> None:
        fut = asyncio.get_event_loop().create_future()
        self._gates[task.task_id] = fut
        self._pending_tasks[task.task_id] = task
        self.state.add_pending_approval(self._pending_approval_record(task))
        self._log_event(
            "approval_requested",
            self._artifact_preview(task),
            task.origin,
            {
                "task_id": task.task_id,
                "gate_name": self._approval_required_event(task).payload.get(
                    "gate_name"
                ),
            },
        )
        await self._emit(self._approval_required_event(task))

    def _close_approval_gate(self, task_id: str) -> None:
        self._gates.pop(task_id, None)
        self._pending_tasks.pop(task_id, None)
        self.state.remove_pending_approval(task_id)

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

    def _engineering_infra_revision_task(self, d: Deliverable) -> CompanyTask:
        context = dict(d.metadata.get("context", {}))
        context["infra_error"] = d.content
        return CompanyTask(
            task_id=d.task_id,
            origin="devops",
            target="engineering",
            artifact_type="deploy_request",
            payload={
                "deploy_report": d.content,
                "deploy_metadata": dict(d.metadata),
                "instruction": "Address the infrastructure deployment failure and resubmit the implementation.",
            },
            blocking=False,
            context=context,
        )

    def _devops_task(self, task: CompanyTask) -> CompanyTask:
        context = dict(task.context)
        if self.active_project:
            context.setdefault("project_name", self.active_project.name)
        return CompanyTask(
            task_id=task.task_id,
            origin="ceo",
            target="devops",
            artifact_type="deploy_request",
            payload={
                "engineering_result": (
                    self.active_project.engineering_result.content
                    if self.active_project and self.active_project.engineering_result
                    else None
                ),
                "engineering_metadata": (
                    dict(self.active_project.engineering_result.metadata)
                    if self.active_project and self.active_project.engineering_result
                    else {}
                ),
                "qa_report": (
                    task.payload.get("qa_report")
                    if isinstance(task.payload, dict)
                    else task.payload
                ),
                "qa_metadata": (
                    task.payload.get("qa_metadata", {})
                    if isinstance(task.payload, dict)
                    else {}
                ),
                "environment": context.get("environment", "production"),
            },
            blocking=False,
            context=context,
        )

    async def _submit_devops_after_release(self, task: CompanyTask) -> None:
        if self.active_project:
            self.state.set_current_phase("deploying")
        if "devops" in self.departments:
            await self.submit(self._devops_task(task))
        elif self.active_project:
            self.state.set_current_phase("done")

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
                "handoff_to": "devops",
            },
        )
        await self._open_approval_gate(task)
        decision = self._normalize_decision(await self._gates[task.task_id])
        self._close_approval_gate(task.task_id)

        if decision.approved:
            await self._submit_devops_after_release(task)
            return

        if self.active_project:
            self.state.set_current_phase("development")
        await self._route_release_rejection(task, decision)

    def _pending_approval_record(self, task: CompanyTask) -> dict:
        event = self._approval_required_event(task)
        return {
            "task_id": task.task_id,
            "gate_name": event.payload.get("gate_name"),
            "department": task.origin,
            "artifact_preview": event.payload.get("artifact_preview", ""),
            "timestamp": self.state.pending_approval_timestamp(),
            "status": "pending",
            "task": self._serialize_task(task),
        }

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
            "payload": self.state.json_safe(task.payload),
            "blocking": task.blocking,
            "context": self.state.json_safe(task.context),
        }

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
            and task.target in {"engineering", "ux"}
            and task.artifact_type == "prd"
        ):
            project.prd = self._prd_content(task)
            self.state.set_current_phase(
                "design" if task.target == "ux" else "development"
            )

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
            self.state.set_current_phase("prototyping")

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
        await self._dispatch(revision_task)

    async def _route_release_rejection(
        self, task: CompanyTask, decision: ApprovalDecision
    ) -> None:
        if "engineering" not in self.departments:
            return
        feedback = (
            decision.metadata.get("feedback") or decision.reason or "Rejected by CEO"
        )
        await self._dispatch(
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

    async def _run_post_mortem(self, d: Deliverable) -> None:
        project = self.active_project
        if project is None:
            return

        playbook = self.state.get_playbook()
        playbook.setdefault("coding_standards", [])
        playbook.setdefault("ux_preferences", [])
        playbook.setdefault("deployment_gotchas", [])

        if project.qa_result and project.qa_result.status == "failure":
            self._append_unique_playbook_item(
                playbook["coding_standards"],
                f"Previous QA failed for {project.name}: {project.qa_result.content}",
            )

        if d.department == "devops" and d.status == "failure":
            self._append_unique_playbook_item(
                playbook["deployment_gotchas"],
                f"Previous deployment failed for {project.name}: {d.content}",
            )

        for record in self.state.get_audit_log():
            if (
                not isinstance(record, dict)
                or record.get("event_type") != "approval_resolved"
            ):
                continue
            metadata = record.get("metadata", {})
            if not isinstance(metadata, dict) or metadata.get("approved") is not False:
                continue
            feedback = metadata.get("reason") or metadata.get("feedback")
            if feedback:
                self._append_unique_playbook_item(
                    playbook["ux_preferences"],
                    f"CEO feedback from a previous approval: {feedback}",
                )

        self.state.save_playbook(playbook)
        self._log_event(
            "post_mortem_completed",
            {"project_id": project.project_id, "phase": project.phase},
            "orchestrator",
            {"task_id": d.task_id},
        )

    @staticmethod
    def _append_unique_playbook_item(items: list, value: str) -> None:
        value = str(value)[:500]
        if value and value not in items:
            items.append(value)

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
        self._log_event("approval_resolved", payload, department, event_metadata)

    def _log_event(
        self,
        event_type: str,
        payload,
        department: str = "orchestrator",
        metadata: Optional[dict] = None,
    ) -> None:
        try:
            self.state.append_audit_event(
                department=department,
                event_type=event_type,
                payload=payload,
                metadata=metadata,
            )
        except Exception:
            pass

    async def start(self) -> None:
        await asyncio.gather(
            *[dept.run_loop() for dept in self.departments.values()],
            return_exceptions=True,
        )
