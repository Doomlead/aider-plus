from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Awaitable, Callable, Dict, List, Optional, Union

from aider.memory import ProjectMemory
from aider.memory.pattern_extractor import AuditPatternExtractor
from aider.company.approval import ApprovalManager
from aider.company.config import CompanyConfig, default_company_config
from aider.company.context import ContextBuilder
from aider.company.department import Department
from aider.company.events import EventBus, event_from_legacy_message, global_event_bus
from aider.company.lifecycle import LifecycleEngine
from aider.company.playbook import PlaybookManager
from aider.company.self_improvement import SelfImprovementService
from aider.company.skills import CompanySkillManager
from aider.company.project import Project
from aider.company.state import CompanyStateManager
from aider.company.schemas import (
    ApprovalDecision,
    CompanyEvent,
    CompanyTask,
    Deliverable,
    EventMessage,
    ProjectPlan,
)

CompanyMessage = Union[Deliverable, EventMessage]
logger = logging.getLogger(__name__)


class CompanyOrchestrator:
    def __init__(
        self,
        project_memory: ProjectMemory,
        company_config: CompanyConfig | None = None,
        event_bus: EventBus | None = None,
    ):
        self.company_config = company_config or default_company_config()
        self.state = CompanyStateManager(project_memory)
        self.context_builder = ContextBuilder(
            self.state, self.company_config.skill_learning
        )
        self.lifecycle = LifecycleEngine(self.state)
        self.approvals = ApprovalManager(self.state, audit_logger=self._log_event)
        self.approval_manager = self.approvals
        self.approvals.on_event(self._emit)
        self.departments: Dict[str, Department] = {}
        self.event_bus = event_bus or global_event_bus
        self.session_id = f"company:{getattr(project_memory, 'repo_path', 'memory')}"
        self._handlers: List[Callable[[CompanyMessage], Awaitable[None]]] = []
        self._error_handlers: List[Callable[[str], Awaitable[None]]] = []
        self._background_tasks: set[asyncio.Task] = set()
        self._shutdown = False

    @property
    def _gates(self):
        return self.approvals.gates

    @property
    def _resolved_task_ids(self):
        return self.approvals.resolved_task_ids

    @property
    def _pending_tasks(self):
        return self.approvals._pending_tasks

    @property
    def _recovered_gate_tasks(self):
        return self.approvals._recovered_gate_tasks

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
        self._apply_department_config(dept.name, dept)

        def route_deliverable(deliverable: Deliverable) -> None:
            self.create_task(
                self._route(deliverable),
                label=f"route {dept.name} deliverable {deliverable.task_id}",
            )

        dept._on_deliverable = route_deliverable

        async def submit_task(task: CompanyTask) -> Optional[Deliverable]:
            return await self.submit(task)

        dept._submit_task = submit_task
        dept._on_event = self._emit

    def _apply_department_configs(self) -> None:
        """Wire CompanyConfig values into registered department agent loops."""
        for dept_name, department in self.departments.items():
            self._apply_department_config(dept_name, department)

    def _apply_department_config(self, dept_name: str, department: Department) -> None:
        """Wire one DepartmentConfig into a department-owned agent loop."""
        dept_config = self.company_config.get_department_config(dept_name)
        department.config = dept_config
        department.agent_config = dept_config
        agent_loop = getattr(department, "agent_loop", None)
        if agent_loop is None:
            return

        if hasattr(agent_loop, "mcp_approval_handler"):
            agent_loop.mcp_approval_handler = self._request_mcp_tool_approval
        mcp_manager = getattr(agent_loop, "mcp_manager", None)
        if mcp_manager is not None:
            mcp_manager.approval_handler = self._request_mcp_tool_approval

        agent_loop.enable_prompt_caching = dept_config.enable_caching
        loop_config = getattr(agent_loop, "config", None)
        if loop_config is not None:
            loop_config.enable_caching = dept_config.enable_caching
            loop_config.cache_type = dept_config.cache_type
            loop_config.mcp = self.company_config.mcp

        if loop_config is not None:
            loop_config.department_config = dept_config

        reviewer_config = self.company_config.departments.get("reviewer")
        if reviewer_config is not None:
            agent_loop.reviewer_department_config = reviewer_config
            if (
                loop_config is not None
                and getattr(loop_config, "reviewer_model", None) is None
            ):
                loop_config.reviewer_model = reviewer_config.preferred_model

        if dept_config.preferred_model:
            if hasattr(agent_loop, "model"):
                agent_loop.model = dept_config.preferred_model
            elif loop_config is not None and hasattr(loop_config, "model"):
                loop_config.model = dept_config.preferred_model

    def create_task(self, coro, label: Optional[str] = None) -> asyncio.Task:
        """Create and track an orchestrator-owned background task."""
        if self._shutdown:
            raise RuntimeError("Company orchestrator is shutting down")
        task = asyncio.create_task(coro, name=label)
        self._background_tasks.add(task)

        def _done(completed: asyncio.Task) -> None:
            self._background_tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                completed.result()
            except Exception as err:
                logger.exception(
                    "Company orchestrator background task failed: %s",
                    label or completed.get_name(),
                )
                if not self._shutdown:
                    self.create_task(
                        self._emit_background_error(label or completed.get_name(), err),
                        label="report orchestrator background error",
                    )

        task.add_done_callback(_done)
        return task

    async def _request_mcp_tool_approval(self, request: dict) -> bool:
        """Create a human approval gate for high-risk MCP tool calls."""
        server = str(request.get("server", "unknown"))
        tool = str(request.get("tool", "unknown"))
        arguments = request.get("arguments", {})
        gate_task = CompanyTask(
            task_id=f"mcp-approval-{uuid.uuid4().hex[:12]}",
            origin="mcp",
            target="engineering",
            artifact_type="mcp_tool_call",
            payload={"server": server, "tool": tool, "arguments": arguments},
            blocking=True,
            context={
                "gate_name": "mcp_tool_approval",
                "approver_role": "ceo",
                "artifact_preview": json.dumps(
                    {"server": server, "tool": tool, "arguments": arguments},
                    sort_keys=True,
                )[:1500],
                "handoff_to": "engineering",
            },
        )
        decision = await self.approvals.create_request(gate_task)
        self.approvals.close_request(gate_task.task_id)
        return bool(decision.approved)

    async def shutdown(self) -> None:
        """Cancel orchestrator-owned tasks and wait for graceful exit."""
        self._shutdown = True
        tasks = [task for task in self._background_tasks if not task.done()]
        tasks.extend(
            task
            for task in self.approvals._recovered_gate_tasks.values()
            if not task.done()
        )
        for gate in self.approvals.gates.values():
            if not gate.done():
                gate.cancel()
        for task in tasks:
            task.cancel()
        if tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self.approvals._recovered_gate_tasks.clear()

    def on_deliverable(self, handler: Callable[[CompanyMessage], Awaitable[None]]):
        self._handlers.append(handler)

    def on_background_error(self, handler: Callable[[str], Awaitable[None]]) -> None:
        self._error_handlers.append(handler)

    async def _emit_background_error(self, label: str, err: Exception) -> None:
        message = f"{label} failed: {err}"
        for handler in self._error_handlers:
            try:
                await handler(message)
            except Exception:
                logger.exception("Company background error handler failed")

    async def _emit(self, message: CompanyMessage) -> None:
        await self.event_bus.publish_async(
            event_from_legacy_message(message, session_id=self.session_id)
        )
        # Stream to legacy Discord / event listeners
        for handler in self._handlers:
            try:
                await handler(message)
            except Exception:
                logger.exception("Company event handler failed")

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
        self._record_token_usage(d)
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
            # Product determined the request was too vague. Raise an approval
            # gate so the CEO can answer the questions, then re-submit to Product.
            if d.artifact_type == "clarification":
                clarification_task = CompanyTask(
                    task_id=d.task_id,
                    origin="product",
                    target="product",
                    artifact_type="clarification",
                    payload=d.payload,
                    blocking=True,
                    context={
                        **d.metadata.get("context", {}),
                        "gate_name": "clarification_approval",
                        "approver_role": "ceo",
                        "artifact_preview": d.metadata.get(
                            "artifact_preview", d.payload
                        ),
                        "clarification_questions": d.metadata.get(
                            "clarification_questions", []
                        ),
                        "original_request": d.metadata.get("original_request", ""),
                        "handoff_to": "product",
                    },
                )
                decision = await self.approvals.create_request(clarification_task)
                self.approvals.close_request(clarification_task.task_id)
                if decision.approved:
                    # CEO answered — re-submit to Product with the answer as context.
                    ceo_answer = (
                        decision.reason
                        or decision.metadata.get("reason")
                        or decision.metadata.get("feedback")
                        or ""
                    )
                    original_request = d.metadata.get("original_request", "")
                    resubmit_context = dict(d.metadata.get("context", {}))
                    resubmit_context["clarification_answers"] = ceo_answer
                    resubmit_task = CompanyTask(
                        task_id=d.task_id,
                        origin="ceo",
                        target="product",
                        artifact_type="raw_prompt",
                        payload={
                            "original_request": original_request,
                            "clarification_answers": ceo_answer,
                            "prompt": (
                                f"{original_request}\n\n"
                                f"CEO clarification answers:\n{ceo_answer}"
                            ),
                        },
                        blocking=False,
                        context=resubmit_context,
                    )
                    if "product" in self.departments:
                        await self.submit(resubmit_task)
                return True

            if d.artifact_type == "prd" and d.status == "success":
                project.prd = str(d.content)
                project.requires_design = bool(d.metadata.get("requires_design", False))
                if "delivery" in self.departments:
                    await self.submit(
                        self._proactive_delivery_task(d, phase="prototyping")
                    )
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
                if "delivery" in self.departments:
                    await self.submit(self._proactive_delivery_task(d, phase="design"))
                self.lifecycle.apply(
                    self.lifecycle.transition_for_deliverable(project, d)
                )
                if "engineering" in self.departments:
                    await self.submit(self._handoff_task(d, "engineering"))
                return True

        if project.phase == "development" and d.department == "engineering":
            project.engineering_result = d
            if d.status == "success":
                self.lifecycle.apply(
                    self.lifecycle.transition_for_deliverable(project, d)
                )
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

            # Record QA outcome for pass-rate tracking.
            qa_test_passed = (
                d.payload.get("test_passed") if isinstance(d.payload, dict) else None
            )
            self.state.record_qa_result(qa_test_passed)

            qa_meta = d.metadata or {}
            if qa_meta.get("handoff_to") == "engineering" and not qa_meta.get(
                "blocking", True
            ):
                qa_feedback_dict = qa_meta.get("qa_feedback") or {}
                revision_number = qa_feedback_dict.get("revision_number", 1)

                # Guard: don't loop forever
                max_qa_revisions = 3
                if revision_number > max_qa_revisions:
                    await self._emit(
                        EventMessage(
                            event=CompanyEvent.PROJECT_BLOCKED,
                            task_id=d.task_id,
                            payload={
                                "reason": f"QA failed after {max_qa_revisions} "
                                "revision cycles.",
                                "last_feedback": qa_feedback_dict,
                            },
                        )
                    )
                    self.state.set_current_phase("release_ready")
                    return True

                self.state.set_current_phase("development")
                if "engineering" in self.departments:
                    self.state.record_task_iteration(qa_revision=True)
                    await self.submit(self._qa_revision_task(d, qa_feedback_dict))
                return True

            if "delivery" in self.departments:
                self.lifecycle.apply(
                    self.lifecycle.transition_for_deliverable(project, d)
                )
                await self.submit(self._delivery_task(d))
            else:
                self.state.set_current_phase("release_ready")
                await self._request_release_approval(d)
            return True

        if d.department == "delivery":
            project.delivery_result = d
            plan_dict = (
                d.metadata.get("project_plan") if isinstance(d.metadata, dict) else None
            )
            if isinstance(plan_dict, dict):
                project.delivery_plan = ProjectPlan.from_dict(plan_dict)
            self._log_event(
                "delivery_plan_ready" if d.status == "success" else "delivery_blocked",
                d.payload,
                d.department,
                {"task_id": d.task_id, "status": d.status},
            )
            if project.phase != "delivery":
                return True
            if d.status != "success":
                self.state.set_current_phase("development")
                if "engineering" in self.departments:
                    await self.submit(self._engineering_revision_task(d))
                return True
            if not await self.delivery_readiness_gate(d):
                return True
            self.lifecycle.apply(self.lifecycle.transition_for_deliverable(project, d))
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
            self.lifecycle.apply(self.lifecycle.transition_for_deliverable(project, d))
            await self._run_post_mortem(d)
            self.lifecycle.apply(
                self.lifecycle.transition_after_post_mortem(project.phase, d)
            )
            if d.status == "success":
                return True

            if "engineering" in self.departments:
                await self.submit(self._engineering_infra_revision_task(d))
            return True

        return False

    def _handoff_task(self, d: Deliverable, next_target: str) -> CompanyTask:
        payload = d.payload
        context = dict(d.metadata.get("context", {}))
        metadata = dict(d.metadata or {})

        if (
            d.department == "product"
            and d.artifact_type == "prd"
            and next_target
            in {
                "engineering",
                "ux",
            }
        ):
            prd_content = d.payload if isinstance(d.payload, str) else str(d.payload)
            payload = {
                "original_request": metadata.get("original_request"),
                "prd_content": prd_content,
                "prd_summary": prd_content,
                "prd_structured": metadata.get("prd_structured") or metadata.get("prd"),
                "prd_metadata": metadata,
                "open_questions": metadata.get("open_questions", []),
            }
            context.update(payload)
        elif d.department == "ux" and next_target == "engineering":
            prd_content = context.get("prd_content") or ""
            design_spec_md = d.payload if isinstance(d.payload, str) else str(d.payload)
            design_spec_structured = (
                d.metadata.get("design_spec_structured")
                or context.get("design_spec_structured")
                or context.get("design_spec")
            )
            design_spec_summary = (
                d.metadata.get("design_spec_summary")
                or context.get("design_spec_summary")
                or self._synthesize_design_spec_summary(design_spec_structured)
            )

            payload = {
                "original_request": context.get("original_request"),
                "prd_content": prd_content,
                "prd_structured": context.get("prd_structured"),
                "prd_summary": context.get("prd_summary")
                or self._synthesize_prd_summary(context),
                "design_spec": design_spec_md,
                "design_spec_structured": design_spec_structured,
                "design_spec_summary": design_spec_summary,
                "ux_self_review": d.metadata.get("ux_self_review_passed"),
                "schema_gate_approved": d.metadata.get("schema_gate_approved"),
                "design_spec_validation_errors": (
                    d.metadata.get("validation_errors")
                    or context.get("design_spec_validation_errors")
                ),
                "design_metadata": dict(d.metadata),
            }
            if payload["ux_self_review"] is None:
                payload["ux_self_review"] = d.metadata.get("self_review_notes")
            if payload["ux_self_review"] is None:
                payload["ux_self_review"] = d.metadata.get("self_review")
            if payload["schema_gate_approved"] is None:
                payload["schema_gate_approved"] = context.get("schema_gate_approved")
            if payload["design_spec_summary"] is None and isinstance(d.payload, str):
                payload["design_spec_summary"] = design_spec_md
            metadata.setdefault("next_artifact_type", "design_spec")
            context.update(payload)
        elif (
            d.department == "product"
            and next_target == "engineering"
            and d.artifact_type == "memo"
        ):
            payload = {
                "clarification_response": d.content,
                "clarification_metadata": metadata,
                **context,
            }

        if "prd_summary" not in context and "prd_structured" in context:
            prd = context["prd_structured"]
            if isinstance(prd, dict):
                context["prd_summary"] = (
                    f"{prd.get('title', '')}\n"
                    f"{prd.get('overview') or prd.get('problem_statement', '')}\n"
                    f"Requirements: {prd.get('requirements') or prd.get('acceptance_criteria', [])}"
                )

        task = CompanyTask(
            task_id=d.task_id,
            origin=d.department,
            target=next_target,
            artifact_type=metadata.get("next_artifact_type", "general"),
            payload=payload,
            blocking=metadata.get("blocking", False),
            context=context,
        )
        task.context.setdefault("prd_summary", context.get("prd_summary"))
        task.context.setdefault(
            "design_spec_structured", context.get("design_spec_structured")
        )
        task.context["playbook_guidance"] = self._get_relevant_playbooks(task)
        task.context["skill_guidance"] = self._get_relevant_skills(task)
        return task

    def _synthesize_design_spec_summary(self, spec) -> str | None:
        """Build a compact UX handoff summary for DesignSpecV2 and legacy specs."""
        if not isinstance(spec, dict):
            return None

        parts = [f"Title: {spec.get('title', 'Untitled Design')}"]
        if overview := spec.get("overview"):
            parts.append(f"Overview: {str(overview)[:200]}")

        screens = spec.get("screens") or spec.get("key_screens")
        if screens:
            parts.append(f"Screens: {len(screens)}")

        components = spec.get("components") or spec.get("component_library")
        if components:
            parts.append(f"Components: {len(components)}")

        if a11y := spec.get("accessibility_checklist") or spec.get(
            "accessibility_notes"
        ):
            parts.append(f"Accessibility: {str(a11y)[:150]}")

        return "\n".join(parts)

    def _synthesize_prd_summary(self, context: dict) -> str:
        """Build a concise PRD summary from structured context when none was provided."""
        prd = context.get("prd_structured")
        if isinstance(prd, dict):
            return (
                f"{prd.get('title', '')}\n"
                f"{prd.get('overview') or prd.get('problem_statement', '')}\n"
                f"Requirements: {prd.get('requirements') or prd.get('acceptance_criteria', [])}"
            )
        return str(context.get("prd_content") or "")

    def _get_relevant_playbooks(self, task: CompanyTask) -> list[str]:
        """Return playbook guidance relevant to an orchestrator handoff task."""
        existing = task.context.get("playbook_guidance") if task.context else None
        if existing:
            return existing if isinstance(existing, list) else [str(existing)]
        playbook = self.context_builder._requested_playbook(["playbook.*"], task)
        return self.context_builder._format_playbook_guidance(playbook)

    def _get_relevant_skills(self, task: CompanyTask) -> list[str]:
        """Return role-scoped procedural skill guidance relevant to a handoff."""
        existing = task.context.get("skill_guidance") if task.context else None
        if existing:
            return existing if isinstance(existing, list) else [str(existing)]
        if not self.company_config.skill_learning.enabled:
            return []
        manager = CompanySkillManager(self.state, self.company_config.skill_learning)
        skills = list(manager.query_for_task(task, role=task.target))
        if task.target == "delivery":
            seen = {(skill.scope, skill.name) for skill in skills}
            for skill in manager.query_for_task(task, role="project_management"):
                if (skill.scope, skill.name) not in seen:
                    skills.append(skill)
                    seen.add((skill.scope, skill.name))
        manager.record_skill_usage(skills, role=task.target)
        return manager.format_skill_guidance(skills)

    async def _dispatch(self, task: CompanyTask) -> None:
        department = self.departments[task.target]
        task.context = self.context_builder.build(
            task,
            department.get_context_requirements(),
            self.active_project,
        )
        await department.receive(task)

    async def submit(self, task: CompanyTask) -> Optional[Deliverable]:
        # Only count top-level tasks (non-revision, non-internal handoffs).
        if task.origin in {"ceo", "discord", "cli"}:
            self.state.increment_task_count()
        self.state.record_phase_turn(self.state.get_current_phase(), task.target)
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
            decision = await self.approvals.create_request(task)
            self.approvals.close_request(task.task_id)
            if not decision.approved:
                if self._is_release_approval(task):
                    if self.active_project:
                        self.lifecycle.apply(
                            self.lifecycle.transition_after_approval(
                                self.active_project, task, False
                            )
                        )
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
        await self.approvals.recover_pending_approvals(
            self._complete_recovered_approval
        )

    async def _complete_recovered_approval(
        self, task: CompanyTask, decision: ApprovalDecision
    ) -> None:
        if not decision.approved:
            if self._is_release_approval(task):
                if self.active_project:
                    self.lifecycle.apply(
                        self.lifecycle.transition_after_approval(
                            self.active_project, task, False
                        )
                    )
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

    async def _open_approval_gate(self, task: CompanyTask) -> None:
        await self.approvals.create_request(task)

    def _close_approval_gate(self, task_id: str) -> None:
        self.approvals.close_request(task_id)

    def _approval_required_event(self, task: CompanyTask) -> EventMessage:
        return self.approvals.approval_required_event(task)

    @staticmethod
    def _artifact_preview(task: CompanyTask) -> str:
        return ApprovalManager.artifact_preview(task)

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

    def _qa_revision_task(self, d: Deliverable, qa_feedback_dict: dict) -> CompanyTask:
        revision_number = qa_feedback_dict.get("revision_number", 1)
        context = dict(d.metadata.get("context", {}))
        context.update(
            {
                "qa_feedback": qa_feedback_dict,
                "revision_number": revision_number,
            }
        )
        engineering_result = (
            self.active_project.engineering_result if self.active_project else None
        )
        return CompanyTask(
            task_id=f"{d.task_id}_qa_revision_{revision_number}",
            origin="qa",
            target="engineering",
            artifact_type="prd",
            payload={
                "qa_report": d.content,
                "qa_metadata": dict(d.metadata),
                "qa_feedback": qa_feedback_dict,
                "previous_engineering_result": (
                    engineering_result.content if engineering_result else None
                ),
                "engineering_metadata": (
                    dict(engineering_result.metadata) if engineering_result else {}
                ),
                "prd_content": self.active_project.prd if self.active_project else "",
                "instruction": "Fix the QA failures and resubmit the implementation.",
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
                "instruction": (
                    "Address the infrastructure deployment failure and resubmit "
                    "the implementation."
                ),
            },
            blocking=False,
            context=context,
        )

    def _proactive_delivery_task(self, d: Deliverable, phase: str) -> CompanyTask:
        context = dict(d.metadata.get("context", {}))
        if self.active_project:
            context.setdefault("project_name", self.active_project.name)
            context.setdefault("project_phase", phase or self.active_project.phase)
        payload = {
            "prd_content": (
                self.active_project.prd if self.active_project else d.content
            ),
            "source_department": d.department,
            "source_artifact_type": d.artifact_type,
            "source_metadata": dict(d.metadata),
        }
        if d.department == "ux":
            payload["design_spec"] = d.content
            context.setdefault(
                "design_spec_summary",
                d.metadata.get("design_spec_summary") or d.content,
            )
        task = CompanyTask(
            task_id=f"{d.task_id}:delivery-plan",
            origin=d.department,
            target="delivery",
            artifact_type=(
                d.artifact_type
                if d.artifact_type in {"prd", "design_spec"}
                else "general"
            ),
            payload=payload,
            blocking=False,
            context=context,
        )
        task.context["playbook_guidance"] = self._get_relevant_playbooks(task)
        task.context["skill_guidance"] = self._get_relevant_skills(task)
        return task

    def _delivery_task(self, d: Deliverable) -> CompanyTask:
        context = dict(d.metadata.get("context", {}))
        if self.active_project:
            context.setdefault("project_name", self.active_project.name)
            context.setdefault("project_phase", self.active_project.phase)
        task = CompanyTask(
            task_id=d.task_id,
            origin="qa",
            target="delivery",
            artifact_type="test_report",
            payload={
                "qa_report": d.content,
                "qa_metadata": dict(d.metadata),
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
                "prd_content": self.active_project.prd if self.active_project else "",
            },
            blocking=False,
            context=context,
        )
        task.context["playbook_guidance"] = self._get_relevant_playbooks(task)
        task.context["skill_guidance"] = self._get_relevant_skills(task)
        return task

    def _devops_task(self, task: CompanyTask) -> CompanyTask:
        context = dict(task.context)
        if self.active_project:
            context.setdefault("project_name", self.active_project.name)
        return CompanyTask(
            task_id=task.task_id,
            origin="delivery",
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
                "delivery_plan": (
                    task.payload.get("delivery_plan")
                    if isinstance(task.payload, dict)
                    and task.payload.get("delivery_plan")
                    else (
                        self.active_project.delivery_plan.to_dict()
                        if self.active_project and self.active_project.delivery_plan
                        else context.get("delivery_plan")
                    )
                ),
                "delivery_metadata": (
                    task.payload.get("delivery_metadata", {})
                    if isinstance(task.payload, dict)
                    else {}
                ),
                "delivery_handover": (
                    task.payload.get("delivery_handover")
                    if isinstance(task.payload, dict)
                    and task.payload.get("delivery_handover")
                    else context.get("delivery_handover")
                ),
                "environment": context.get("environment", "production"),
            },
            blocking=False,
            context=context,
        )

    async def delivery_readiness_gate(self, item) -> bool:
        """Validate Delivery readiness before DevOps receives a release task."""
        metadata = getattr(item, "metadata", {}) or {}
        payload = getattr(item, "payload", {}) or {}
        if isinstance(payload, dict):
            delivery_handover = payload.get("delivery_handover")
            delivery_metadata = payload.get("delivery_metadata") or {}
            if not delivery_handover and isinstance(delivery_metadata, dict):
                delivery_handover = delivery_metadata.get("delivery_handover")
        else:
            delivery_handover = None
            delivery_metadata = {}
        if not delivery_handover and isinstance(metadata, dict):
            delivery_handover = metadata.get("delivery_handover")
        if (
            not delivery_handover
            and self.active_project
            and self.active_project.delivery_plan
        ):
            plan = self.active_project.delivery_plan
            delivery_handover = {
                "ready_for_devops": plan.status == "complete"
                and not plan.critical_blockers,
                "critical_blockers": plan.critical_blockers,
                "delivery_summary": plan.to_summary(),
                "go_no_go_recommendation": (
                    "GO"
                    if plan.status == "complete" and not plan.critical_blockers
                    else "NO-GO"
                ),
            }
        if not delivery_handover:
            return "delivery" not in self.departments
        ready = (
            bool(delivery_handover.get("ready_for_devops"))
            if isinstance(delivery_handover, dict)
            else False
        )
        blockers = (
            delivery_handover.get("critical_blockers", [])
            if isinstance(delivery_handover, dict)
            else []
        )
        if ready and not blockers:
            return True
        await self._emit(
            EventMessage(
                event=CompanyEvent.PROJECT_BLOCKED,
                task_id=getattr(item, "task_id", "delivery-handoff"),
                payload={
                    "reason": "Delivery handoff is not ready for DevOps.",
                    "critical_blockers": blockers,
                    "delivery_handover": delivery_handover,
                },
            )
        )
        if self.active_project:
            self.state.set_current_phase("delivery")
        return False

    async def _handle_delivery_handoff(self, item) -> bool:
        """Backward-compatible alias for the Delivery readiness gate."""
        return await self.delivery_readiness_gate(item)

    async def _submit_devops_after_release(self, task: CompanyTask) -> None:
        if not await self.delivery_readiness_gate(task):
            return
        if self.active_project:
            self.lifecycle.apply(
                self.lifecycle.transition_after_approval(
                    self.active_project, task, True
                )
            )
        await self._execute_devops_release(task)

    async def _execute_devops_release(self, task: CompanyTask) -> None:
        """Route a validated Delivery handoff into DevOps build/deploy execution."""
        if not await self.delivery_readiness_gate(task):
            return
        if "devops" in self.departments:
            await self.submit(self._devops_task(task))
        elif self.active_project:
            self.state.set_current_phase("done")

    async def _request_release_approval(self, d: Deliverable) -> None:
        task = CompanyTask(
            task_id=f"{d.task_id}:release",
            origin=d.department,
            target="engineering",
            artifact_type="test_report",
            payload={
                "qa_report": d.content,
                "qa_metadata": dict(d.metadata),
                "delivery_plan": d.content if d.department == "delivery" else None,
                "delivery_metadata": (
                    dict(d.metadata) if d.department == "delivery" else {}
                ),
                "delivery_handover": (
                    d.metadata.get("delivery_handover")
                    if d.department == "delivery"
                    else None
                ),
            },
            blocking=True,
            context={
                **dict(d.metadata.get("context", {})),
                "gate_name": "release_approval",
                "artifact_preview": d.content,
                "handoff_to": "devops",
            },
        )
        decision = await self.approvals.create_request(task)
        self.approvals.close_request(task.task_id)

        if decision.approved:
            await self._submit_devops_after_release(task)
            return

        if self.active_project:
            self.lifecycle.apply(
                self.lifecycle.transition_after_approval(
                    self.active_project, task, False
                )
            )
        await self._route_release_rejection(task, decision)

    def _record_token_usage(self, d: Deliverable) -> None:
        token_usage = d.metadata.get("token_usage") or d.metadata.get("usage")
        if token_usage is None and isinstance(d.payload, dict):
            token_usage = d.payload.get("token_usage") or d.payload.get("usage")
        model = d.metadata.get("model") or (
            d.payload.get("model") if isinstance(d.payload, dict) else None
        )
        cache_enabled = (
            d.metadata.get("cache_enabled")
            if self.company_config.record_caching_stats
            else None
        )
        self.state.record_department_tokens(
            d.department,
            token_usage,
            model=model,
            cache_enabled=cache_enabled,
        )

    def company_status(self) -> str:
        project = self.active_project
        observability = self.state.get_observability()
        pending = self.state.get_pending_approvals()
        lines = ["Company Dashboard"]
        if project is None:
            lines.append("Project: none")
            lines.append(
                f"Persisted phase: {self.memory.data.get('current_project_phase', 'unknown')}"
            )
        else:
            lines.extend(
                [
                    f"Project: {project.name} ({project.project_id})",
                    f"Phase: {project.phase}",
                    f"Requires design: {project.requires_design}",
                    f"Revision count: {project.revision_count}",
                ]
            )
            artifacts = []
            if project.prd:
                artifacts.append("PRD")
            if project.design_spec:
                artifacts.append("Design spec")
            if project.engineering_result:
                artifacts.append(f"Engineering: {project.engineering_result.status}")
            if project.qa_result:
                artifacts.append(f"QA: {project.qa_result.status}")
            if getattr(project, "delivery_result", None):
                artifacts.append(f"Delivery: {project.delivery_result.status}")
            if project.deploy_result:
                artifacts.append(f"Deployment: {project.deploy_result.status}")
            lines.append(
                "Artifacts: " + (", ".join(artifacts) if artifacts else "none")
            )
            if project.deploy_result and isinstance(
                project.deploy_result.payload, dict
            ):
                build = project.deploy_result.payload.get("build_artifact") or {}
                deploy = project.deploy_result.payload.get("deployment_result") or {}
                lines.extend(
                    [
                        "DevOps release:",
                        (
                            f"  Build: {build.get('name', 'artifact')}:"
                            f"{build.get('tag', 'untagged')} "
                            f"({build.get('artifact_type', 'unknown')})"
                        ),
                        f"  Artifact: {build.get('location', 'unknown')}",
                        (
                            "  Deploy status: "
                            f"{deploy.get('status', project.deploy_result.status)}"
                        ),
                        (
                            "  URL: "
                            f"{deploy.get('deployed_url') or project.deploy_result.metadata.get('deploy_url') or 'n/a'}"
                        ),
                        (
                            "  Logs: "
                            f"{(project.deploy_result.payload.get('build_logs_summary') or build.get('build_logs_summary') or 'n/a')[:180]}"
                        ),
                    ]
                )
                log_artifacts = (
                    project.deploy_result.payload.get("log_artifacts")
                    or project.deploy_result.metadata.get("log_artifacts")
                    or []
                )
                if log_artifacts:
                    lines.append(
                        "  Log artifacts: " + ", ".join(map(str, log_artifacts[:3]))
                    )
            if getattr(project, "delivery_plan", None):
                delivery = project.delivery_plan.to_summary()
                blockers = delivery.get("critical_blockers") or []
                blocker_text = ", ".join(map(str, blockers)) if blockers else "none"
                blocker_prefix = "🔴 " if blockers else ""
                lines.extend(
                    [
                        "Delivery:",
                        f"  Status: {delivery.get('status') or delivery.get('overall_status')}",
                        f"  Completion: {delivery.get('weighted_completion', delivery.get('completion_percentage', 0))}%",
                        f"  Next milestone: {delivery.get('next_milestone', 'TBD')}",
                        f"  Critical blockers: {blocker_prefix}{blocker_text}",
                    ]
                )
        lines.append("Departments: " + (", ".join(sorted(self.departments)) or "none"))
        caching_agents = []
        for name in sorted({"coo", *self.departments.keys()}):
            agent_config = self.company_config.get_department_config(name)
            marker = "on" if agent_config.enable_caching else "off"
            caching_agents.append(f"{name}:{marker}")
        lines.append(
            "Prompt caching: "
            + (", ".join(caching_agents) if caching_agents else "none")
        )
        lines.append(f"Pending approvals: {len(pending)}")
        if pending:
            for approval in pending:
                lines.append(
                    "  - "
                    + str(approval.get("gate_name", "approval"))
                    + " task="
                    + str(approval.get("task_id", "unknown"))
                )
        obs = observability
        lines.append(
            "Turns per phase: "
            + json.dumps(obs.get("turns_per_phase", {}), sort_keys=True)
        )

        # Structured token usage per department
        usage_map = obs.get("token_usage_per_department", {})
        if usage_map:
            lines.append("Token usage per department:")
            for dept in sorted(usage_map):
                rec = usage_map[dept]
                if isinstance(rec, dict):
                    total = rec.get("total_tokens", 0)
                    cost = rec.get("estimated_cost_usd", 0.0)
                    runs = rec.get("run_count", 0)
                    cached = rec.get("cached_runs", 0)
                    uncached = rec.get("uncached_runs", 0)
                    cache_info = (
                        f", {cached} cached/{uncached} uncached"
                        if (cached + uncached) > 0
                        else ""
                    )
                    lines.append(
                        f"  {dept}: {total:,} tokens "
                        f"(~${cost:.4f} USD, {runs} runs{cache_info})"
                    )
                else:
                    lines.append(f"  {dept}: {rec} tokens")
        else:
            lines.append("Token usage per department: (none recorded)")

        # QA pass-rate
        qa_m = obs.get("qa_metrics", {})
        if qa_m.get("total_runs", 0) > 0:
            lines.append(
                f"QA pass rate: {qa_m['pass_rate']:.1%} "
                f"({qa_m.get('passed', 0)} passed / "
                f"{qa_m.get('failed', 0)} failed / "
                f"{qa_m.get('no_tests', 0)} no-tests "
                f"out of {qa_m['total_runs']} runs)"
            )

        # Task iteration metrics
        tm = obs.get("task_metrics", {})
        if tm.get("total_tasks", 0) > 0:
            lines.append(
                f"Tasks: {tm['total_tasks']} total, "
                f"QA revision cycles: {tm.get('qa_revision_cycles', 0)}, "
                f"Avg QA revisions/task: {tm.get('avg_qa_revisions', 0.0):.2f}"
            )

        return "\n".join(lines)

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
        return self.approvals.serialize_task(task)

    @staticmethod
    def _is_release_approval(task: CompanyTask) -> bool:
        context = task.context if isinstance(task.context, dict) else {}
        return context.get("gate_name") == "release_approval"

    @staticmethod
    def _normalize_decision(decision) -> ApprovalDecision:
        return ApprovalManager.normalize_decision(decision)

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
            self.lifecycle.apply(
                self.lifecycle.transition_after_approval(project, task, True)
            )

    async def _route_rejection(
        self, task: CompanyTask, decision: ApprovalDecision
    ) -> None:
        if task.origin not in self.departments:
            return

        revision_count = 1
        previous_metadata = {}
        previous_prd_structured = None
        previous_prd_summary = ""
        if isinstance(task.payload, dict):
            previous_metadata = dict(task.payload.get("prd_metadata", {}))
            previous_prd_structured = (
                task.payload.get("prd_structured")
                or previous_metadata.get("prd_structured")
                or previous_metadata.get("prd")
            )
            previous_prd_summary = str(
                task.payload.get("prd_summary")
                or previous_metadata.get("previous_prd_summary")
                or task.payload.get("prd_content")
                or ""
            )
            revision_count = int(previous_metadata.get("revision_count", 0) or 0) + 1

        if self.active_project and task.origin == "product":
            self.lifecycle.apply(
                self.lifecycle.transition_after_approval(
                    self.active_project, task, False
                )
            )

        feedback = (
            decision.metadata.get("feedback") or decision.reason or "Rejected by CEO"
        )
        reviewer_notes = (
            decision.metadata.get("reviewer_notes")
            or decision.metadata.get("notes")
            or ""
        )
        revision_task = CompanyTask(
            task_id=task.task_id,
            origin="ceo",
            target=task.origin,
            artifact_type="prd",
            payload={
                "previous_prd": self._prd_content(task),
                "previous_prd_structured": previous_prd_structured,
                "previous_prd_summary": previous_prd_summary,
                "ceo_feedback": feedback,
                "reviewer_notes": reviewer_notes,
                "revision_count": revision_count,
                "original_request": previous_metadata.get("original_request", ""),
            },
            blocking=False,
            context={
                **task.context,
                "ceo_feedback": feedback,
                "reviewer_notes": reviewer_notes,
                "revision_count": revision_count,
                "previous_prd_summary": previous_prd_summary,
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
        return await self.approvals.handle_approval_response(
            task_id, approved, source=source, reason=reason, metadata=metadata
        )

    def approve(self, task_id: str, metadata: Optional[dict] = None) -> None:
        self.approvals.approve(task_id, metadata=metadata)

    def reject(
        self,
        task_id: str,
        reason: str = "Rejected by CEO",
        metadata: Optional[dict] = None,
    ) -> None:
        self.approvals.reject(task_id, reason=reason, metadata=metadata)

    def request_changes(self, task_id: str, feedback: str) -> None:
        self.approvals.request_changes(task_id, feedback)

    async def _run_post_mortem(self, d: Deliverable) -> None:
        project = self.active_project
        if project is None:
            return

        playbook_manager = PlaybookManager(self.state)

        # --- Extract structured patterns from the full audit log ---
        extractor = AuditPatternExtractor(
            self.state.get_audit_log(),
            project_name=project.name,
            project_id=project.project_id,
        )
        patterns = extractor.extract()

        # --- Also synthesize from live deliverable state ---
        # QA failure summary (richer than what's in the audit log alone)
        if project.qa_result and project.qa_result.status == "failure":
            qa_payload = project.qa_result.payload
            if isinstance(qa_payload, dict):
                failed_tests = qa_payload.get("qa_feedback", {}) or {}
                if isinstance(failed_tests, dict):
                    failed_list = failed_tests.get("failed_tests", [])
                else:
                    failed_list = []
                test_results = str(qa_payload.get("test_results", ""))[:300]
            else:
                failed_list = []
                test_results = str(qa_payload)[:300]

            if failed_list:
                tests_str = ", ".join(str(t) for t in failed_list[:3])
                summary = (
                    f"QA failure in '{project.name}': tests [{tests_str}]. "
                    f"{test_results}"
                )
            else:
                summary = f"QA failure in '{project.name}': {test_results}"

            if summary:
                playbook_manager.append_raw("coding_standards", summary)

        # Deployment failure from the live devops deliverable
        if d.department == "devops" and d.status == "failure":
            deploy_text = (
                str(d.payload.get("summary", ""))
                if isinstance(d.payload, dict)
                else str(d.payload)
            )
            if deploy_text:
                playbook_manager.append_raw(
                    "deployment_gotchas",
                    f"Deployment failure in '{project.name}': {deploy_text[:300]}",
                )

        # --- Merge all extracted patterns (with dedup and size cap) ---
        playbook_manager.merge_patterns(patterns)

        # --- Additive procedural-memory proposals (approval-gated by default) ---
        skill_proposals = SelfImprovementService(
            self.state, self.company_config.skill_learning
        ).learn_from_post_mortem(project, d)

        self._log_event(
            "post_mortem_completed",
            {
                "project_id": project.project_id,
                "phase": project.phase,
                "patterns_extracted": {
                    cat: len(pats) for cat, pats in patterns.items()
                },
                "skill_proposals_created": len(skill_proposals),
            },
            "orchestrator",
            {"task_id": d.task_id},
        )

    @staticmethod
    def _append_unique_playbook_item(items: list, value: str) -> None:
        """Deprecated: use PlaybookManager.append_raw() instead."""
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
        metadata = metadata or {}
        try:
            self.state.append_audit_event(
                department=department,
                event_type=event_type,
                payload=payload,
                metadata=metadata,
            )
        except Exception:
            logger.exception("Failed to append company audit event")

        try:
            asyncio.get_running_loop()
            event_name = self._stream_event_name(event_type, department)
            message = EventMessage(
                event=event_name,
                task_id=str(metadata.get("task_id", "")),
                payload={
                    "event_type": event_type,
                    "department": department,
                    "payload": payload,
                },
                metadata=dict(metadata),
            )
            self.create_task(
                self._emit(message),
                label=f"stream company event {event_name}",
            )
        except RuntimeError:
            pass

    @staticmethod
    def _stream_event_name(event_type: str, department: str) -> str:
        if event_type == "approval_requested":
            return "approval_requested"
        if event_type == "task_submitted" and department == "product":
            return "planning"
        if event_type == "deliverable_produced" and department == "engineering":
            return "engineering_complete"
        if event_type == "deliverable_produced" and department == "qa":
            return "qa_complete"
        if event_type == "deliverable_produced" and department == "delivery":
            return "delivery_plan_ready"
        if event_type == "deliverable_produced" and department == "devops":
            return "deployment_complete"
        return event_type

    async def start(self) -> None:
        await asyncio.gather(
            *[dept.run_loop() for dept in self.departments.values()],
            return_exceptions=True,
        )
