#!/usr/bin/env python

import asyncio
import atexit
import concurrent.futures
import logging
import os
import random
import sys
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from aider import models, urls
from aider.agent.loop import AgentLoopConfig
from aider.coders import Coder
from aider.company.agent_factory import build_company_agent_loops
from aider.company.audit import AuditLogViewer
from aider.company.config import apply_agent_model_overrides_from_env
from aider.company.coo import NanobotCOO
from aider.company.events import CompanyEvent as RuntimeCompanyEvent
from aider.company.daemon import CompanyDaemonError, load_daemon
from aider.company.knowledge import KnowledgeManager
from aider.company.departments.delivery import DeliveryDepartment
from aider.company.departments.devops import DevOpsDepartment
from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.product import ProductDepartment
from aider.company.departments.qa import QADepartment
from aider.company.departments.security_app import AppSecDepartment
from aider.company.departments.security_platform import PlatformSecDepartment
from aider.company.departments.ux import UXDepartment
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.workflow import WorkflowError
from aider.company.project import Project
from aider.company.skills import CompanySkillManager
from aider.company.schemas import CompanyEvent, CompanyTask, EventMessage
from aider.company.surface_messages import format_runtime_event_message
from aider.dump import dump  # noqa: F401
from aider.io import InputOutput
from aider.main import main as cli_main
from aider.memory import ConversationMemory, ProjectMemory, consolidate_conversation
from aider.scrape import Scraper, has_playwright
from aider.gui_settings_manager import (
    SETTINGS_SECTIONS,
    SettingsAgentForm,
    SettingsForm,
    build_settings_preview,
    load_settings_form,
    save_settings,
)
from aider.settings import COMPANY_AGENT_NAMES

logger = logging.getLogger(__name__)
_COMPANY_SESSIONS = {}
_COMPANY_SESSIONS_LOCK = threading.Lock()

AGENT_DISPLAY_NAMES = {
    "coo": "COO",
    "ux": "UX",
    "qa": "QA",
    "delivery": "Delivery",
    "devops": "DevOps",
    "security_app": "AppSec",
    "security_platform": "PlatformSec",
}


def company_agent_display_name(agent_name: str) -> str:
    normalized = str(agent_name or "").strip().lower()
    return AGENT_DISPLAY_NAMES.get(normalized, normalized.replace("_", " ").title())


AGENT_CHAT_TARGETS = [
    "Direct Aider",
    "Company Workflow",
    *[company_agent_display_name(name) for name in COMPANY_AGENT_NAMES],
]

AGENT_ICONS = {
    "Direct Aider": "💬",
    "Company Workflow": "🏢",
    "COO": "🧭",
    "Product": "📋",
    "UX": "🎨",
    "Engineering": "🛠️",
    "Reviewer": "🔎",
    "QA": "✅",
    "Delivery": "🗓️",
    "DevOps": "🚀",
    "AppSec": "🛡️",
    "PlatformSec": "🔐",
}


COMPANY_PHASES = [
    "prototyping",
    "design",
    "development",
    "qa",
    "delivery",
    "release_ready",
    "deploying",
    "post_mortem",
    "done",
]


def humanize_company_label(value) -> str:
    return str(value or "unknown").replace("_", " ").title()


def truncate_preview(value, limit: int = 1600) -> str:
    preview = str(value or "").strip()
    if len(preview) > limit:
        return preview[:limit] + "…"
    return preview


class CaptureIO(InputOutput):
    lines = []

    def tool_output(self, msg, log_only=False):
        if not log_only:
            self.lines.append(msg)
        super().tool_output(msg, log_only=log_only)

    def tool_error(self, msg):
        self.lines.append(msg)
        super().tool_error(msg)

    def tool_warning(self, msg):
        self.lines.append(msg)
        super().tool_warning(msg)

    def get_captured_lines(self):
        lines = self.lines
        self.lines = []
        return lines


def search(text=None):
    results = []
    for root, _, files in os.walk("aider"):
        for file in files:
            path = os.path.join(root, file)
            if not text or text in path:
                results.append(path)
    # dump(results)

    return results


# Keep state as a resource, which survives browser reloads (since Coder does too)
class State:
    keys = set()

    def init(self, key, val=None):
        if key in self.keys:
            return

        self.keys.add(key)
        setattr(self, key, val)
        return True


@st.cache_resource
def get_state():
    return State()


@st.cache_resource
def get_coder():
    coder = cli_main(return_coder=True)
    if not isinstance(coder, Coder):
        raise ValueError(coder)
    if not coder.repo:
        raise ValueError("GUI can currently only be used inside a git repo")

    io = CaptureIO(
        pretty=False,
        yes=True,
        dry_run=coder.io.dry_run,
        encoding=coder.io.encoding,
    )
    # coder.io = io # this breaks the input_history
    coder.commands.io = io

    for line in coder.get_announcements():
        coder.io.tool_output(line)

    return coder


class DesktopCompanySession:
    """Desktop/Streamlit façade over the same Company workflow used by Discord."""

    def __init__(self, coder: Coder):
        self.coder = coder
        self.repo_path = str(Path(coder.root).resolve())
        self.events = []
        self.event_queue = deque(maxlen=200)
        self.event_version = 0
        self.event_lock = threading.Lock()
        self.pending_runs = []
        self.service_tasks = []
        self.background_error = None
        self._shutdown = False
        self._event_bus_unsubscribe = None
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(
            target=self._run_loop,
            name="aider-desktop-company",
            daemon=True,
        )
        self.loop_thread.start()

        self._ensure_memories()
        self.orchestrator = None
        self.coo = None
        self.product = None
        self.ux = None
        self.engineering = None
        self.qa = None
        self.devops = None
        self.agent_loops = {}
        self.active_project = Project(
            project_id=str(uuid.uuid4()),
            name=Path(self.repo_path).name,
            phase="prototyping",
        )
        self._init_company_session()
        atexit.register(self.shutdown)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self.loop.close()

    def _ensure_memories(self):
        if not getattr(self.coder, "conversation_memory", None):
            self.coder.conversation_memory = ConversationMemory()
        project_memory = getattr(self.coder, "project_memory", None)
        if not isinstance(project_memory, ProjectMemory):
            project_memory = ProjectMemory(self.repo_path)
            self.coder.project_memory = project_memory
        project_memory.load()

    def _init_company_session(self):
        company_config = apply_agent_model_overrides_from_env()
        agent_loops = build_company_agent_loops(
            coder=self.coder,
            company_config=company_config,
            base_config=AgentLoopConfig(use_architect_mode=True),
        )
        self.agent_loops = agent_loops
        project_memory = self.coder.project_memory
        conversation_memory = self.coder.conversation_memory
        self.engineering = EngineeringDepartment(
            project_memory=project_memory,
            agent_loop=agent_loops["engineering"],
            conversation_memory=conversation_memory,
        )
        self.product = ProductDepartment(
            project_memory=project_memory,
            agent_loop=agent_loops["product"],
            conversation_memory=conversation_memory,
        )
        self.ux = UXDepartment(
            project_memory=project_memory,
            agent_loop=agent_loops["ux"],
            conversation_memory=conversation_memory,
        )
        self.qa = QADepartment(
            project_memory=project_memory,
            agent_loop=agent_loops["qa"],
        )
        self.delivery = DeliveryDepartment(
            project_memory=project_memory,
            agent_loop=agent_loops["delivery"],
        )
        self.devops = DevOpsDepartment(
            project_memory=project_memory,
            agent_loop=agent_loops["devops"],
        )
        self.security_app = AppSecDepartment(
            project_memory=project_memory,
            agent_loop=agent_loops["security_app"],
            conversation_memory=conversation_memory,
        )
        self.security_platform = PlatformSecDepartment(
            project_memory=project_memory,
            agent_loop=agent_loops["security_platform"],
            conversation_memory=conversation_memory,
        )
        self.orchestrator = CompanyOrchestrator(
            project_memory=project_memory,
            company_config=company_config,
        )
        self.orchestrator.active_project = self.active_project
        self._event_bus_unsubscribe = self.orchestrator.event_bus.subscribe(
            self._record_runtime_event
        )
        for department in (
            self.product,
            self.ux,
            self.engineering,
            self.qa,
            self.delivery,
            self.devops,
            self.security_app,
            self.security_platform,
        ):
            self.orchestrator.register(department)
        self.coo = NanobotCOO(
            orchestrator=self.orchestrator,
            agent_loop=agent_loops["coo"],
        )
        for department in (
            self.product,
            self.ux,
            self.engineering,
            self.qa,
            self.delivery,
            self.devops,
            self.security_app,
            self.security_platform,
        ):
            self.submit_background(
                department.run_loop(), f"{department.name} run loop", service=True
            )
        self.orchestrator.on_deliverable(self._record_company_message)
        self.orchestrator.on_background_error(self._record_background_error)
        self.submit_background(
            self.orchestrator.recover_pending_approvals(),
            "Recover pending approvals",
            service=True,
        )

    async def _record_runtime_event(self, event):
        with self.event_lock:
            self.events.append(event)
            self.event_queue.append(event)
            self.event_version += 1

    async def _record_company_message(self, message):
        with self.event_lock:
            self.events.append(message)
            self.event_queue.append(message)
            self.event_version += 1

    async def _record_background_error(self, message: str):
        self.background_error = message

    async def _run_background(self, coro, label):
        task = asyncio.create_task(coro, name=label)
        try:
            return await task
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self.background_error = f"{label or 'Background task'} failed: {err}"
            logger.exception("Desktop company background task failed: %s", label)
            raise

    def submit_background(self, coro, label=None, service=False):
        if self._shutdown:
            raise RuntimeError("Desktop company session is shut down")
        future = asyncio.run_coroutine_threadsafe(
            self._run_background(coro, label or "Company background task"), self.loop
        )
        future.add_done_callback(lambda done: self._log_background_result(label, done))
        if service:
            self.service_tasks.append((label or "Company service", future))
        elif label:
            self.pending_runs.append((label, future))
        return future

    def _log_background_result(self, label, future):
        if future.cancelled():
            logger.debug("Desktop company background task cancelled: %s", label)
            return
        try:
            future.result()
        except concurrent.futures.CancelledError:
            logger.debug("Desktop company background task cancelled: %s", label)
        except Exception as err:
            self.background_error = f"{label or 'Background task'} failed: {err}"
            logger.exception("Desktop company background task failed: %s", label)

    def drain_events(self):
        with self.event_lock:
            events = list(self.event_queue)
            self.event_queue.clear()
            return events, self.event_version

    def current_phase(self):
        project = self.active_project
        if project is not None:
            return project.phase
        return self.coder.project_memory.data.get("current_project_phase", "unknown")

    def active_run_count(self):
        return sum(1 for _, future in self.pending_runs if not future.done())

    def is_active(self):
        if self._shutdown:
            return False
        return any(not future.done() for _, future in self.service_tasks)

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        if self._event_bus_unsubscribe is not None:
            self._event_bus_unsubscribe()
            self._event_bus_unsubscribe = None
        futures = [future for _, future in self.pending_runs + self.service_tasks]
        if self.orchestrator and self.loop.is_running():
            try:
                shutdown_future = asyncio.run_coroutine_threadsafe(
                    self.orchestrator.shutdown(), self.loop
                )
                shutdown_future.result(timeout=5)
            except Exception:
                logger.exception("Desktop company orchestrator shutdown failed")
        for future in futures:
            future.cancel()
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if (
            self.loop_thread.is_alive()
            and threading.current_thread() is not self.loop_thread
        ):
            self.loop_thread.join(timeout=5)
        with _COMPANY_SESSIONS_LOCK:
            if _COMPANY_SESSIONS.get(self.repo_path) is self:
                _COMPANY_SESSIONS.pop(self.repo_path, None)

    def start_prototype(self, prompt: str):
        return self.submit_background(self._run_prototype(prompt), "Prototype")

    def run_instruction(self, prompt: str):
        return self.submit_background(self._run_instruction(prompt), "Engineering")

    async def receive_human_input(self, prompt: str):
        if self.active_project.phase == "prototyping" and not self.active_project.prd:
            return await self._run_prototype(prompt)
        return await self._run_instruction(prompt)

    def run_auto(self, prompt: str):
        return self.submit_background(self.receive_human_input(prompt), "Company")

    async def _run_prototype(self, prompt: str):
        task = CompanyTask(
            task_id=str(uuid.uuid4()),
            origin="ceo",
            target="product",
            artifact_type="raw_prompt",
            payload=prompt,
            blocking=False,
            context={"project_name": Path(self.repo_path).name},
        )
        coo_result = await self.coo.receive_user_message(
            message=prompt,
            session_id=f"desktop:{self.repo_path}",
            surface="desktop",
            target="product",
            context={"project_name": Path(self.repo_path).name},
            task_id=task.task_id,
            origin=task.origin,
        )
        deliverable = coo_result["deliverable"]
        return {
            "summary": deliverable.payload,
            "content": deliverable.payload,
            "artifact_type": deliverable.artifact_type,
            "status": deliverable.status,
            "metadata": deliverable.metadata,
        }

    async def _run_instruction(self, prompt: str):
        task = CompanyTask(
            task_id=str(uuid.uuid4()),
            origin="ceo",
            target="engineering",
            artifact_type="raw_prompt",
            payload=prompt,
            blocking=False,
        )
        coo_result = await self.coo.receive_user_message(
            message=prompt,
            session_id=f"desktop:{self.repo_path}",
            surface="desktop",
            target="engineering",
            task_id=task.task_id,
            origin=task.origin,
        )
        deliverable = coo_result["deliverable"]
        result = {
            "summary": deliverable.payload,
            "content": deliverable.payload,
            "files": deliverable.metadata.get("files", []),
            "files_changed": deliverable.metadata.get("files", []),
            "commits": deliverable.metadata.get("commits", []),
            "diffs": deliverable.metadata.get("diffs", []),
            "status": deliverable.status,
        }
        self.coder.project_memory.update(
            {"last_prompt": prompt, "last_result": deliverable.payload}
        )
        return result

    def approve(self, task_id: str, feedback: str = ""):
        metadata = {"approved_by": "desktop"}
        if feedback:
            metadata.update({"reason": feedback, "feedback": feedback})
        return self.submit_background(
            self.orchestrator.handle_approval_response(
                task_id,
                True,
                source="desktop",
                reason=feedback or None,
                metadata=metadata,
            ),
            f"Approve {task_id}",
        )

    def reject(self, task_id: str, reason: str = "Rejected from desktop"):
        return self.submit_background(
            self.orchestrator.handle_approval_response(
                task_id,
                False,
                source="desktop",
                reason=reason,
                metadata={"action": "reject", "feedback": reason},
            ),
            f"Reject {task_id}",
        )

    def request_changes(self, task_id: str, feedback: str):
        return self.submit_background(
            self.orchestrator.handle_approval_response(
                task_id,
                False,
                source="desktop",
                reason=feedback,
                metadata={"action": "revise", "feedback": feedback},
            ),
            f"Request changes {task_id}",
        )

    async def _run_agent_chat(self, agent_name: str, prompt: str):
        normalized = str(agent_name or "").strip().lower()
        loop = self.agent_loops.get(normalized)
        if loop is None:
            raise ValueError(f"Unknown company agent: {agent_name}")
        return await loop.run(prompt)

    def chat_with_agent(self, agent_name: str, prompt: str):
        return self.submit_background(
            self._run_agent_chat(agent_name, prompt),
            f"{company_agent_display_name(agent_name)} agent chat",
        )

    def pending_approvals(self):
        return self.orchestrator.state.get_pending_approvals()

    def audit_log(self, limit: int = 10) -> str:
        return AuditLogViewer.from_project_memory(
            self.coder.project_memory
        ).render_text(limit=limit)

    def company_status(self) -> str:
        return self.orchestrator.company_status()

    def caching_status(self) -> str:
        config = self.orchestrator.company_config
        roles = sorted({"coo", *self.orchestrator.departments.keys()})
        states = []
        for role in roles:
            agent_config = config.get_department_config(role)
            states.append(f"{role}:{'on' if agent_config.enable_caching else 'off'}")
        return ", ".join(states) or "none"

    def coo_status(self) -> dict:
        if self.coo is None:
            return {}
        session_id = f"desktop:{self.repo_path}"
        future = asyncio.run_coroutine_threadsafe(
            self.coo.get_session_status(session_id), self.loop
        )
        return future.result(timeout=5)

    def skills_summary(self) -> dict:
        return CompanySkillManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        ).dashboard_summary()

    def get_knowledge_overview(self, query: str = "") -> dict:
        return KnowledgeManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        ).get_overview(query=query)

    def approve_skill_proposal(self, proposal_id: str) -> dict:
        return KnowledgeManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        ).approve_skill_proposal(proposal_id)

    def reject_skill_proposal(
        self, proposal_id: str, reason: str = "Rejected from Knowledge"
    ) -> dict:
        return KnowledgeManager(
            self.orchestrator.state, self.orchestrator.company_config.skill_learning
        ).reject_skill_proposal(proposal_id, reason=reason)

    def daemon_status(self) -> dict[str, Any]:
        workflow_path = Path(self.repo_path) / "AIDER_WORKFLOW.md"
        if not workflow_path.exists():
            return {
                "configured": False,
                "workflow": str(workflow_path),
                "status": "not configured",
                "running": False,
                "last_run": None,
                "active_workflows": 0,
                "pending_proof_of_work": 0,
                "recent_proof_of_work": [],
            }
        try:
            status = load_daemon(workflow_path).get_status()
        except (CompanyDaemonError, WorkflowError, OSError, ValueError) as err:
            return {
                "configured": True,
                "workflow": str(workflow_path),
                "status": f"unavailable: {err}",
                "running": False,
                "last_run": None,
                "active_workflows": 0,
                "pending_proof_of_work": 0,
                "recent_proof_of_work": [],
            }
        status["configured"] = True
        return status

    def system_overview(self) -> dict:
        pending = [
            approval
            for approval in self.pending_approvals()
            if approval.get("status") == "pending"
        ]
        try:
            coo_status = self.coo_status()
        except Exception as err:
            coo_status = {"status": f"unavailable: {err}"}
        mcp_config = getattr(self.orchestrator.company_config, "mcp", None)
        warehouse_registry = Path(self.repo_path) / "products" / "warehouse.json"
        active_products = "None"
        if warehouse_registry.exists():
            active_products = str(warehouse_registry)
        daemon = self.daemon_status()
        deploy_result = getattr(self.active_project, "deploy_result", None)
        deploy_payload = (
            getattr(deploy_result, "payload", {})
            if deploy_result and isinstance(getattr(deploy_result, "payload", {}), dict)
            else {}
        )
        build_artifact = deploy_payload.get("build_artifact") or {}
        deployment_result = deploy_payload.get("deployment_result") or {}
        log_artifacts = deploy_payload.get("log_artifacts") or (
            getattr(deploy_result, "metadata", {}) or {}
        ).get("log_artifacts", [])
        last_build = {
            "status": (
                getattr(deploy_result, "status", "not run")
                if deploy_result
                else "not run"
            ),
            "artifact": build_artifact.get("location")
            or deploy_payload.get("release_artifact"),
            "artifact_type": build_artifact.get("artifact_type"),
            "git_tag": build_artifact.get("tag") or deploy_payload.get("git_tag"),
            "environment": (
                deployment_result.get("environment")
                or deploy_payload.get("environment")
            ),
            "deploy_url": deployment_result.get("deployed_url")
            or deploy_payload.get("deploy_url"),
            "provider": (
                (deployment_result.get("target") or {}).get("provider")
                if isinstance(deployment_result.get("target"), dict)
                else (
                    (deploy_payload.get("deployment_target") or {}).get("provider")
                    if isinstance(deploy_payload.get("deployment_target"), dict)
                    else "local"
                )
            ),
            "logs_url": deployment_result.get("logs_url")
            or deploy_payload.get("logs_url"),
            "deployed_at": deployment_result.get("deployed_at")
            or deploy_payload.get("deployed_at"),
            "deployment_notes": deployment_result.get("deployment_notes")
            or deploy_payload.get("deployment_notes"),
            "rollback_command": (
                deployment_result.get("rollback_command")
                or deploy_payload.get("rollback_command")
            ),
            "logs_summary": (
                deploy_payload.get("build_logs_summary")
                or build_artifact.get("build_logs_summary")
                or deployment_result.get("deployment_logs")
            ),
            "log_artifacts": (
                log_artifacts if isinstance(log_artifacts, list) else [log_artifacts]
            ),
        }
        delivery_plan = getattr(self.active_project, "delivery_plan", None)
        delivery_summary = (
            delivery_plan.to_summary()
            if delivery_plan is not None
            else (
                getattr(
                    getattr(self.active_project, "delivery_result", None),
                    "metadata",
                    {},
                )
                or {}
            ).get("delivery_summary", {})
        )
        return {
            "caching": self.caching_status(),
            "coo_status": coo_status.get("status", "unknown"),
            "coo_last_action": (coo_status.get("last_coo_action") or {}).get(
                "action", "—"
            ),
            "delivery_status": (
                delivery_summary.get("overall_status")
                or getattr(
                    getattr(self.active_project, "delivery_result", None),
                    "status",
                    None,
                )
                or ("active" if self.current_phase() == "delivery" else "not started")
            ),
            "delivery_summary": delivery_summary,
            "delivery_completion": delivery_summary.get("completion_percentage", 0),
            "delivery_next_milestone": delivery_summary.get("next_milestone", "TBD"),
            "delivery_critical_blockers": delivery_summary.get("critical_blockers", []),
            "pending_escalations": len(pending),
            "daemon": daemon,
            "daemon_status": daemon.get("status", "unknown"),
            "daemon_last_run": daemon.get("last_run") or "never",
            "daemon_active_workflows": daemon.get("active_workflows", 0),
            "daemon_pending_proof_of_work": daemon.get("pending_proof_of_work", 0),
            "daemon_recent_proof_of_work": daemon.get("recent_proof_of_work", []),
            "daemon_recent_runs": daemon.get("runs", [])[-5:],
            "security_status": (
                (self.orchestrator.memory.data.get("security", {}) or {}).get(
                    "status", "not_scanned"
                )
                if isinstance(self.orchestrator.memory.data, dict)
                else "not_scanned"
            ),
            "security_severity": (
                (self.orchestrator.memory.data.get("security", {}) or {}).get(
                    "severity", "info"
                )
                if isinstance(self.orchestrator.memory.data, dict)
                else "info"
            ),
            "security_findings": (
                (self.orchestrator.memory.data.get("security", {}) or {}).get(
                    "finding_count", 0
                )
                if isinstance(self.orchestrator.memory.data, dict)
                else 0
            ),
            "security_posture": (
                (self.orchestrator.memory.data.get("security", {}) or {}).get(
                    "posture", "⚪ Not scanned"
                )
                if isinstance(self.orchestrator.memory.data, dict)
                else "⚪ Not scanned"
            ),
            "security_last_scan_at": (
                (self.orchestrator.memory.data.get("security", {}) or {}).get(
                    "last_scan_at", "never"
                )
                if isinstance(self.orchestrator.memory.data, dict)
                else "never"
            ),
            "security_next_scan_at": (
                (self.orchestrator.memory.data.get("security", {}) or {}).get(
                    "next_scan_at", "unscheduled"
                )
                if isinstance(self.orchestrator.memory.data, dict)
                else "unscheduled"
            ),
            "security_open_critical": (
                (self.orchestrator.memory.data.get("security", {}) or {}).get(
                    "open_critical_count", 0
                )
                if isinstance(self.orchestrator.memory.data, dict)
                else 0
            ),
            "security_open_high": (
                (self.orchestrator.memory.data.get("security", {}) or {}).get(
                    "open_high_count", 0
                )
                if isinstance(self.orchestrator.memory.data, dict)
                else 0
            ),
            "security_recent_patches": (
                (self.orchestrator.memory.data.get("security", {}) or {}).get(
                    "recent_patches_applied", []
                )
                if isinstance(self.orchestrator.memory.data, dict)
                else []
            ),
            "last_build": last_build,
            "last_build_status": last_build.get("status", "not run"),
            "last_build_artifact": last_build.get("artifact") or "n/a",
            "last_build_logs_summary": (
                last_build.get("logs_summary") or "No build logs captured yet."
            ),
            "last_build_log_artifacts": last_build.get("log_artifacts", []),
            "last_deployment_provider": last_build.get("provider", "local"),
            "last_deployment_url": last_build.get("deploy_url"),
            "last_deployment_logs_url": last_build.get("logs_url"),
            "last_deployment_deployed_at": last_build.get("deployed_at"),
            "last_deployment_notes": last_build.get("deployment_notes"),
            "last_deployment_rollback_command": last_build.get("rollback_command"),
            "active_warehouse_products": active_products,
            "mcp_status": (
                f"enabled ({len(getattr(mcp_config, 'servers', {}) or {})} servers)"
                if getattr(mcp_config, "enabled", False)
                else "disabled"
            ),
        }

    def audit_records(self, limit: int = 10) -> list[dict]:
        records = self.coder.project_memory.data.get("audit_log", [])
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)][-limit:]

    def last_activity(self) -> str:
        records = self.audit_records(limit=1)
        if records:
            return str(records[-1].get("timestamp") or "No audit events")
        if self.events:
            event = self.events[-1]
            metadata = getattr(event, "metadata", {})
            if isinstance(metadata, dict) and metadata.get("timestamp"):
                return str(metadata.get("timestamp"))
            return "Live event received"
        return "No activity yet"

    def recent_deliverables(self) -> list[dict]:
        project = self.active_project
        deliverables = []
        if project.prd:
            deliverables.append(
                {
                    "label": "PRD",
                    "department": "Product",
                    "status": "needs review",
                    "summary": project.prd,
                    "files": [],
                }
            )
        if project.design_spec:
            deliverables.append(
                {
                    "label": "Design spec",
                    "department": "UX",
                    "status": "ready",
                    "summary": project.design_spec,
                    "files": [],
                }
            )
        for attr, label in (
            ("engineering_result", "Engineering output"),
            ("qa_result", "QA results"),
            ("delivery_result", "Delivery plan"),
            ("deploy_result", "Deployment"),
        ):
            deliverable = getattr(project, attr, None)
            if not deliverable:
                continue
            metadata = getattr(deliverable, "metadata", {}) or {}
            payload = getattr(deliverable, "payload", "")
            files = metadata.get("files") or metadata.get("files_changed") or []
            extras = {}
            if label == "Deployment" and isinstance(payload, dict):
                extras = {
                    "artifact_links": (
                        payload.get("artifact_links")
                        or [payload.get("release_artifact")]
                    ),
                    "log_artifacts": (
                        payload.get("log_artifacts")
                        or metadata.get("log_artifacts", [])
                    ),
                    "logs_summary": (
                        payload.get("build_logs_summary")
                        or payload.get("deployment_logs_summary")
                    ),
                }
            deliverables.append(
                {
                    "label": label,
                    "department": getattr(deliverable, "department", "company"),
                    "status": getattr(deliverable, "status", "unknown"),
                    "summary": payload,
                    "files": files if isinstance(files, list) else [files],
                    **extras,
                }
            )
        return deliverables[-6:]

    def dashboard_metrics(self, turns_this_session: int = 0) -> dict:
        pending = [
            approval
            for approval in self.pending_approvals()
            if approval.get("status") == "pending"
        ]
        return {
            "turns_this_session": turns_this_session,
            "approvals_pending": len(pending),
            "last_activity": self.last_activity(),
            "current_phase": self.current_phase(),
            "active_runs": self.active_run_count(),
        }

    def persist(self):
        conversation_memory = getattr(self.coder, "conversation_memory", None)
        project_memory = getattr(self.coder, "project_memory", None)
        if isinstance(conversation_memory, ConversationMemory) and isinstance(
            project_memory, ProjectMemory
        ):
            consolidate_conversation(conversation_memory, project_memory)
            project_memory.persist()


def shutdown_desktop_company_session(_coder) -> None:
    repo_path = str(Path(_coder.root).resolve())
    with _COMPANY_SESSIONS_LOCK:
        session = _COMPANY_SESSIONS.get(repo_path)
    if session is not None and not session._shutdown:
        session.shutdown()


def get_desktop_company_session(_coder):
    repo_path = str(Path(_coder.root).resolve())
    with _COMPANY_SESSIONS_LOCK:
        session = _COMPANY_SESSIONS.get(repo_path)
        if session is not None and not session._shutdown:
            return session
        session = DesktopCompanySession(_coder)
        _COMPANY_SESSIONS[repo_path] = session
        return session


def format_desktop_approval(approval: dict) -> str:
    gate_name = approval.get("gate_name", "prd_approval")
    gate_label = (
        gate_name.replace("prd", "PRD").replace("_", " ").title().replace("Prd", "PRD")
    )
    preview = str(approval.get("artifact_preview", "")).strip()
    if len(preview) > 1200:
        preview = preview[:1200] + "…"
    return (
        f"**Gate:** {gate_label}\n\n"
        f"**Department:** `{approval.get('department', 'unknown')}`\n\n"
        f"**Task:** `{approval.get('task_id')}`\n\n"
        f"**Preview:**\n\n{preview or '_No preview available._'}"
    )


class GUI:
    prompt = None
    prompt_as = "user"
    last_undo_empty = None
    recent_msgs_empty = None
    web_content_empty = None

    def announce(self):
        lines = self.coder.get_announcements()
        lines = "  \n".join(lines)
        return lines

    def show_edit_info(self, edit):
        commit_hash = edit.get("commit_hash")
        commit_message = edit.get("commit_message")
        diff = edit.get("diff")
        fnames = edit.get("fnames")
        if fnames:
            fnames = sorted(fnames)

        if not commit_hash and not fnames:
            return

        show_undo = False
        res = ""
        if commit_hash:
            res += f"Commit `{commit_hash}`: {commit_message}  \n"
            if commit_hash == self.coder.last_aider_commit_hash:
                show_undo = True

        if fnames:
            fnames = [f"`{fname}`" for fname in fnames]
            fnames = ", ".join(fnames)
            res += f"Applied edits to {fnames}."

        if diff:
            with st.expander(res):
                st.code(diff, language="diff")
                if show_undo:
                    self.add_undo(commit_hash)
        else:
            with st.container(border=True):
                st.write(res)
                if show_undo:
                    self.add_undo(commit_hash)

    def add_undo(self, commit_hash):
        if self.last_undo_empty:
            self.last_undo_empty.empty()

        self.last_undo_empty = st.empty()
        undone = self.state.last_undone_commit_hash == commit_hash
        if not undone:
            with self.last_undo_empty:
                if self.button(
                    f"Undo commit `{commit_hash}`", key=f"undo_{commit_hash}"
                ):
                    self.do_undo(commit_hash)

    def do_sidebar(self):
        with st.sidebar:
            st.title("Aider")
            self.do_company_tab()
            st.divider()

            if st.button("⚙️ Settings", key="open_settings"):
                self.state.show_settings = not self.state.show_settings
            # self.cmds_tab, self.settings_tab = st.tabs(["Commands", "Settings"])

            if self.state.show_settings:
                self.do_settings_tab()
                st.divider()

            # self.do_recommended_actions()
            self.do_add_to_chat()
            self.do_recent_msgs()
            self.do_clear_chat_history()
            # st.container(height=150, border=False)
            # st.write("### Experimental")

            st.warning(
                "This browser version of aider is experimental. Please share feedback in [GitHub"
                " issues](https://github.com/Aider-AI/aider/issues)."
            )

    def do_company_tab(self):
        st.subheader("Company Mode")
        active_label = "Active" if self.state.company_enabled else "Paused"
        active_icon = "🟢" if self.state.company_enabled else "⏸️"
        st.markdown(f"### {active_icon} {active_label}")
        st.caption(
            "Route chat through the Product → UX → Engineering → QA → DevOps "
            "workflow, or pause it for direct Aider chat."
        )
        with st.expander("🚀 Onboarding / Quick Start", expanded=False):
            st.write(
                "Initialize the Company warehouse, write AIDER_WORKFLOW.md, and save "
                "starter daemon/template/model preferences for this repository."
            )
            if st.button(
                "Create Company quickstart files", key="company_onboarding_quickstart"
            ):
                from aider.company.onboarding import CompanyOnboarding, DEPARTMENTS
                from aider.company.templates import DEFAULT_TEMPLATE_KEY
                from aider.company.warehouse import default_warehouse_path

                defaults = {
                    "warehouse_path": default_warehouse_path(),
                    "template": DEFAULT_TEMPLATE_KEY,
                    "mcp_enabled": False,
                    "model_preferences": {
                        dept: {"model": "", "cache": True} for dept in DEPARTMENTS
                    },
                }
                prompts = iter(["", ""])
                onboarding = CompanyOnboarding(
                    defaults=defaults, input_func=lambda _prompt: next(prompts, "")
                )
                result = onboarding.run_onboarding_flow()
                st.success(f"Wrote quickstart guide: {result.workflow_guide_path}")
            st.code("aider company init", language="bash")

        toggle_enabled = st.toggle(
            "Company Mode Toggle",
            value=self.state.company_enabled,
            disabled=self.prompt_pending(),
            help="Switch between the structured Company workflow and direct Aider chat.",
        )
        if toggle_enabled != self.state.company_enabled:
            self.state.company_enabled = toggle_enabled
            if not toggle_enabled and self.company is not None:
                self.company.shutdown()
                self.company = None
            st.rerun()

        button_label = (
            "⏸️ Stop Company Mode"
            if self.state.company_enabled
            else "▶️ Start Company Mode"
        )
        if st.button(
            button_label,
            key="company_start_stop",
            disabled=self.prompt_pending(),
            use_container_width=True,
        ):
            self.state.company_enabled = not self.state.company_enabled
            if not self.state.company_enabled and self.company is not None:
                self.company.shutdown()
                self.company = None
            st.rerun()

        if self.state.company_enabled:
            st.success("Company workflow is active for new prompts.")
            self.state.company_bypass_next = st.toggle(
                "Bypass Company for next prompt",
                value=self.state.company_bypass_next,
                disabled=self.prompt_pending(),
                help="Send the next chat prompt straight to classic Aider without pausing Company Mode.",
            )
            self.state.company_route = st.selectbox(
                "Company route",
                ["Auto", "Prototype", "Engineering"],
                index=["Auto", "Prototype", "Engineering"].index(
                    self.state.company_route
                ),
                disabled=self.prompt_pending(),
                help=(
                    "Auto mirrors Discord's human entry point. Prototype starts with Product/PRD. "
                    "Engineering skips PRD generation for existing-project tasks."
                ),
            )
            self.state.company_auto_refresh = st.toggle(
                "Auto-refresh company UI",
                value=self.state.company_auto_refresh,
                help=(
                    "Polls the background company session so approvals and dashboard "
                    "status stay current."
                ),
            )
            company = self.get_company()
            self.enable_company_polling(company)
            self.drain_company_events(company)
            self.do_company_pending_runs(company)
            self.do_company_status_sidebar(company)
            if st.button(
                "🔄 Refresh company state",
                key="company_refresh",
                use_container_width=True,
            ):
                st.rerun()
        else:
            st.info(
                "Direct Aider chat is active. Start Company Mode when you want the structured workflow."
            )
            self.state.company_bypass_next = False

    def do_company_status_sidebar(self, company):
        pending_count = len(
            [
                approval
                for approval in company.pending_approvals()
                if approval.get("status") == "pending"
            ]
        )
        st.metric("Current Phase", humanize_company_label(company.current_phase()))
        st.metric("Pending Approvals", pending_count)
        active_runs = company.active_run_count()
        if active_runs:
            st.caption(f"Background runs: {active_runs}")
        if company.background_error:
            st.error(company.background_error)

    def drain_company_events(self, company):
        new_events, event_version = company.drain_events()
        if new_events:
            self.state.company_event_version = event_version
        return new_events

    def enable_company_polling(self, company):
        if not self.state.company_auto_refresh:
            return
        should_poll = (
            company.active_run_count()
            or company.pending_approvals()
            or company.event_queue
            or company.background_error
        )
        if not should_poll:
            return
        components.html(
            """
            <script>
            const waitMs = 2500;
            setTimeout(() => {
              const doc = window.parent.document;
              const buttons = Array.from(doc.querySelectorAll('button'));
              const refresh = buttons.find((button) =>
                (button.innerText || '').includes('Refresh company state')
              );
              if (refresh) { refresh.click(); }
            }, waitMs);
            </script>
            """,
            height=0,
        )

    def do_company_pending_runs(self, company):
        remaining = []
        for label, future in company.pending_runs:
            if future.done():
                try:
                    result = future.result()
                except Exception as err:
                    self.info(f"{label} failed: {err}", echo=False)
                    st.error(f"{label} failed: {err}")
                else:
                    summary = (
                        result.get("summary") if isinstance(result, dict) else result
                    )
                    if summary:
                        msg = f"{label} completed.\n\n{summary}"
                        self.state.messages.append(
                            {"role": "assistant", "content": msg}
                        )
                        st.success(f"{label} completed.")
            else:
                remaining.append((label, future))
                st.info(f"{label} is running in the background.")
        company.pending_runs = remaining

    def do_company_approvals(self, company, prominent: bool = False):
        pending = [
            approval
            for approval in company.pending_approvals()
            if approval.get("status") == "pending"
        ]
        if not pending:
            st.success("No pending approvals.")
            st.caption(
                "Approval requests will appear here with artifact previews and action controls."
            )
            return

        st.error(f"{len(pending)} approval request(s) need a decision.")
        for index, approval in enumerate(pending, start=1):
            task_id = str(approval.get("task_id"))
            gate_name = approval.get("gate_name", "approval")
            department = approval.get("department", "unknown")
            title = (
                f"{index}. {humanize_company_label(gate_name)} · "
                f"{humanize_company_label(department)} · {task_id}"
            )
            container = (
                st.container(border=True)
                if prominent
                else st.expander(title, expanded=True)
            )
            with container:
                if prominent:
                    st.subheader(title)
                cols = st.columns([1, 1, 2])
                cols[0].metric("Gate", humanize_company_label(gate_name))
                cols[1].metric("Department", humanize_company_label(department))
                cols[2].caption(f"Task `{task_id}`")

                with st.expander("Context / preview", expanded=True):
                    st.markdown(format_desktop_approval(approval))
                    task = approval.get("task")
                    if isinstance(task, dict) and task.get("context"):
                        st.write("**Additional context**")
                        st.json(task.get("context"))
                feedback = st.text_area(
                    "Optional feedback",
                    key=f"feedback_{task_id}",
                    placeholder=(
                        "Add notes for approval, describe requested changes, or explain why this is rejected."
                    ),
                )
                is_clarification = gate_name == "clarification_approval"
                col1, col2, col3 = st.columns(3)
                with col1:
                    approve_label = (
                        "✅ Submit Answers" if is_clarification else "✅ Approve"
                    )
                    if st.button(
                        approve_label,
                        key=f"approve_{task_id}",
                        use_container_width=True,
                        help=(
                            "Your answers above will be sent to Product to write the PRD."
                            if is_clarification
                            else None
                        ),
                    ):
                        # For clarification, pass feedback text as the CEO's answers.
                        company.approve(task_id, feedback=feedback or "")
                        st.rerun()
                with col2:
                    changes_label = (
                        "⏩ Skip Clarification"
                        if is_clarification
                        else "📝 Request Changes"
                    )
                    if st.button(
                        changes_label,
                        key=f"changes_{task_id}",
                        use_container_width=True,
                    ):
                        company.request_changes(
                            task_id,
                            feedback
                            or (
                                "Proceed without clarification"
                                if is_clarification
                                else "Changes requested from desktop"
                            ),
                        )
                        st.rerun()
                with col3:
                    if st.button(
                        "❌ Reject", key=f"reject_{task_id}", use_container_width=True
                    ):
                        company.reject(task_id, feedback or "Rejected from desktop")
                        st.rerun()

    def render_company_event(self, event):
        if isinstance(event, RuntimeCompanyEvent):
            message = format_runtime_event_message(event)
            if event.severity == "error":
                st.error(message)
            elif event.severity == "warning":
                st.warning(message)
            else:
                st.info(message)
            return

        if isinstance(event, EventMessage):
            if event.event == CompanyEvent.APPROVAL_REQUIRED:
                st.warning(f"Approval required: {event.task_id}")
                st.json(event.payload)
            elif event.event == CompanyEvent.LIFECYCLE:
                payload = event.payload or {}
                lifecycle_labels = {
                    "product_revision_start": "Product is revising PRD based on feedback…",
                    "product_prd_revised": "Product revised PRD",
                }
                event_name = payload.get("name") or event.event
                label = lifecycle_labels.get(
                    event_name, humanize_company_label(event_name)
                )
                iteration = payload.get("iteration")
                suffix = f" (iteration {iteration})" if iteration is not None else ""
                if payload.get("severity") == "warning":
                    st.warning(f"{label}{suffix}")
                else:
                    st.info(f"{label}{suffix}")
                st.json(payload)
            else:
                st.json(event.__dict__)
            return

        department = getattr(event, "department", "company")
        status = getattr(event, "status", "")
        artifact_type = getattr(event, "artifact_type", "")
        content = getattr(event, "content", None) or getattr(event, "payload", "")
        with st.container(border=True):
            st.write(f"**{department}** {artifact_type} {status}")
            st.write(str(content)[:1200])

    def do_main_tabs(self):
        tabs = st.tabs(
            [
                "Chat",
                "Settings",
                "Company Dashboard",
                "Approvals",
                "Audit Log",
                "Knowledge",
                "Project Memory",
                "Guide",
            ]
        )
        with tabs[0]:
            self.do_chat_tab()
        with tabs[1]:
            self.do_settings_tab(location="main")
        with tabs[2]:
            self.do_company_dashboard_page()
        with tabs[3]:
            self.do_company_approvals_page()
        with tabs[4]:
            self.do_company_audit_log_page()
        with tabs[5]:
            self.do_knowledge_page()
        with tabs[6]:
            self.do_project_memory_page()
        with tabs[7]:
            self.do_guide_page()

    def do_guide_page(self):
        st.header("Aider Plus Guide")
        st.caption("Short, task-oriented help for the browser and desktop UI.")
        with st.expander("1. Choose the right chat target", expanded=True):
            st.markdown(
                "- **Direct Aider** for normal pair-programming.\n"
                "- **Company Workflow** for COO-led Product → UX → Engineering → QA → DevOps work.\n"
                "- **COO** for Nanobot-style CEO briefings, memory, status, tool decisions, and delegation.\n"
                "- **Agent tabs** when you want one role's focused opinion or output.\n"
                "- Use the **Quick Agent Switcher** or bottom target selector to jump quickly."
            )
        with st.expander("2. Understand the COO core", expanded=True):
            st.markdown(
                "The COO path is intentionally small, readable, and research-ready: chat/API/MCP "
                "surfaces send messages in, the agent loop decides whether tools are needed, "
                "memory and skills are pulled into context, and product-building or iteration "
                "requests are forwarded to the orchestrator instead of being reimplemented in a chat app."
            )
        with st.expander("3. Settings safely", expanded=True):
            st.markdown(
                "Use **Preview changes** first. Fix validation errors, then click "
                "**Apply & Restart Company Session** so new agent loops pick up models, keys, "
                "caching, `.env`, and `.aider.conf.yml` changes."
            )
        with st.expander("4. Watch the system", expanded=False):
            st.markdown(
                "The Dashboard shows caching by agent, COO status/last action, human "
                "escalations, warehouse products, MCP status, deliverables, and COO activity. "
                "Errors include recovery suggestions when the COO records them."
            )
        with st.expander("5. Keyboard and recovery tips", expanded=False):
            st.markdown(
                "- Ctrl/Cmd+Enter sends in desktop. Streamlit uses the chat input submit action.\n"
                "- If an agent fails, check Settings preview, Provider Keys, MCP status, and Approvals.\n"
                "- Persist Project Memory before closing if you want the Company context saved immediately."
            )

    def do_chat_tab(self):
        st.caption(
            "Use the tabs below to chat with classic Aider, the full Company workflow, "
            "or any dedicated Company agent. Ctrl/Cmd+Enter sends from the main input."
        )
        active_target = (
            self.state.agent_chat_target
            if self.state.agent_chat_target in AGENT_CHAT_TARGETS
            else "Direct Aider"
        )
        quick_target = st.selectbox(
            "Quick Agent Switcher",
            AGENT_CHAT_TARGETS,
            index=AGENT_CHAT_TARGETS.index(active_target),
            format_func=lambda target: f"{AGENT_ICONS.get(target, '🤖')} {target}",
            key="quick_agent_switcher",
        )
        self.state.agent_chat_target = quick_target
        chat_tabs = st.tabs(
            [
                f"{AGENT_ICONS.get(target, '🤖')} {target}"
                for target in AGENT_CHAT_TARGETS
            ]
        )
        for target, tab in zip(AGENT_CHAT_TARGETS, chat_tabs):
            with tab:
                self.do_agent_chat_tab(target)

    def do_agent_chat_tab(self, target: str):
        self.state.active_agent_chat = target
        if target == "Direct Aider":
            if self.state.company_enabled and not self.state.company_bypass_next:
                st.info(
                    "This tab bypasses Company Mode and sends the prompt straight to classic Aider."
                )
            else:
                st.info("You are chatting directly with classic Aider.")
            self.do_messages_container()
            self.do_agent_chat_box(target)
            return

        if target == "Company Workflow":
            if self.state.company_enabled:
                st.info(
                    "Prompts in this tab are queued into the structured Company workflow "
                    "using the selected Company route."
                )
            else:
                st.info(
                    "This tab starts or reuses the Company backend and queues prompts into "
                    "the structured workflow even if Company Mode is paused globally."
                )
        else:
            st.info(
                f"Prompts in this tab go directly to the {target} agent with its own "
                "saved model, API key, local endpoint notes, and caching settings."
            )
        self.do_agent_messages_container(target)
        self.do_agent_chat_box(target)

    def do_agent_messages_container(self, target: str):
        messages = self.state.agent_messages.setdefault(target, [])
        if not messages:
            st.caption("No messages in this agent tab yet.")
        for msg in messages:
            role = msg.get("role", "assistant")
            avatar = "🧑" if role == "user" else AGENT_ICONS.get(target, "🤖")
            with st.chat_message(
                "user" if role == "user" else "assistant", avatar=avatar
            ):
                st.markdown(msg.get("content", ""))

    def do_agent_chat_box(self, target: str = "Direct Aider"):
        with st.container(border=True):
            st.subheader(f"{AGENT_ICONS.get(target, '🤖')} Chat with {target}")
            if target not in {"Direct Aider", "Company Workflow"}:
                try:
                    status = self.get_company().coo_status()
                    last_action = (status.get("last_coo_action") or {}).get(
                        "action", "No COO action yet"
                    )
                    memory_count = len(status.get("coo_memory") or [])
                    st.caption(
                        f"COO last action: {last_action} · memory notes: {memory_count}"
                    )
                except Exception:
                    st.caption("COO summary will appear after Company Mode starts.")
            else:
                st.caption(
                    "Each target keeps its own chat history. Agent tabs use per-agent settings "
                    "from the Settings page."
                )
            prompt = st.text_area(
                "Agent prompt",
                key=f"agent_prompt_{target}_{len(self.state.agent_messages.get(target, []))}_{len(self.state.messages)}",
                placeholder="Ask this agent to explain, edit, test, or implement something...",
                disabled=self.prompt_pending(),
                help="Ctrl/Cmd+Enter clicks this tab's Send button when your browser permits the shortcut.",
            )
            components.html(
                f"""
                <script>
                const doc = window.parent.document;
                if (!doc.__aiderCtrlEnterBound) {{
                  doc.__aiderCtrlEnterBound = true;
                  doc.addEventListener('keydown', (event) => {{
                    if (!(event.ctrlKey || event.metaKey) || event.key !== 'Enter') return;
                    const active = doc.activeElement;
                    if (!active || active.tagName !== 'TEXTAREA') return;
                    const buttons = Array.from(doc.querySelectorAll('button'));
                    const sendButton = buttons.find((button) =>
                      (button.innerText || '').includes('Send to')
                    );
                    if (sendButton) {{
                      event.preventDefault();
                      sendButton.click();
                    }}
                  }});
                }}
                </script>
                """,
                height=0,
            )
            if st.button(
                f"Send to {target}",
                key=f"send_agent_prompt_{target}",
                disabled=self.prompt_pending() or not prompt.strip(),
                use_container_width=True,
            ):
                self.state.agent_chat_target = target
                self.prompt = prompt.strip()

    def get_company_for_page(self):
        if not self.state.company_enabled:
            st.info(
                "Company Mode is paused. Start it from the sidebar to activate this page."
            )
            return None
        company = self.get_company()
        self.enable_company_polling(company)
        self.drain_company_events(company)
        self.do_company_pending_runs(company)
        if company.background_error:
            st.error(company.background_error)
        return company

    def do_company_dashboard_page(self):
        st.header("Company Dashboard")
        company = self.get_company_for_page()
        if company is None:
            st.caption(
                "Direct chat remains available in the Chat tab while Company Mode is paused."
            )
            return

        metrics = company.dashboard_metrics(turns_this_session=self.count_user_turns())
        overview = company.system_overview()
        st.subheader("System Overview")
        o1, o2, o3, o4, o5, o6 = st.columns(6)
        o1.metric("COO status", overview["coo_status"])
        o2.metric("Last COO action", overview["coo_last_action"])
        o3.metric(
            "Delivery status",
            humanize_company_label(overview["delivery_status"]),
            f"{overview['delivery_completion']}% complete",
        )
        o4.metric(
            "Security",
            str(overview.get("security_status", "not_scanned")).upper(),
            f"{overview.get('security_findings', 0)} findings",
        )
        o5.metric("Pending escalations", overview["pending_escalations"])
        o6.metric("Daemon status", overview["daemon_status"])
        with st.container(border=True):
            st.write(f"**Caching enabled:** {overview['caching']}")
            st.write(
                f"**Active warehouse products:** {overview['active_warehouse_products']}"
            )
            st.write(
                "**Security status:** "
                f"{str(overview.get('security_status', 'not_scanned')).upper()} "
                f"({overview.get('security_severity', 'info')}, {overview.get('security_findings', 0)} findings)"
            )
        with st.container(border=True):
            st.subheader("Security Status")
            st.write(
                f"**Overall posture:** {overview.get('security_posture', '⚪ Not scanned')}"
            )
            st.write(f"**Last scan:** {overview.get('security_last_scan_at', 'never')}")
            st.write(
                f"**Next scheduled scan:** {overview.get('security_next_scan_at', 'unscheduled')}"
            )
            st.write(
                "**Open critical/high findings:** "
                f"{overview.get('security_open_critical', 0)}/{overview.get('security_open_high', 0)}"
            )
            patches = overview.get("security_recent_patches") or []
            if patches:
                st.write("**Recent patches applied:**")
                for patch in patches[-5:]:
                    st.write(
                        f"- {patch.get('finding_id') or patch.get('task_id')}: {patch.get('status')}"
                    )
            else:
                st.write("**Recent patches applied:** none recorded")
            st.write(f"**MCP status:** {overview['mcp_status']}")
            blockers = overview.get("delivery_critical_blockers") or []
            st.write(
                f"**Delivery completion:** {overview.get('delivery_completion', 0)}%"
            )
            st.write(
                f"**Delivery next milestone:** {overview.get('delivery_next_milestone', 'TBD')}"
            )
            st.write(
                "**Delivery critical blockers:** "
                + (", ".join(blockers) if blockers else "None")
            )
            st.write(
                "**Daemon:** "
                f"running={overview['daemon'].get('running', False)}, "
                f"last run={overview['daemon_last_run']}, "
                f"active workflows={overview['daemon_active_workflows']}, "
                f"pending proof-of-work={overview['daemon_pending_proof_of_work']}"
            )
            st.write(f"**Last build status:** {overview['last_build_status']}")
            st.write(
                "**Last deployment:** "
                f"[{overview.get('last_deployment_provider', 'local')}] "
                f"{overview.get('last_deployment_url') or 'n/a'}"
            )
            st.write(
                "**Last Deployed:** "
                f"{overview.get('last_deployment_deployed_at') or 'n/a'}"
            )
            st.write(
                "**Last deployment logs URL:** "
                f"{overview.get('last_deployment_logs_url') or 'n/a'}"
            )
            if overview.get("last_deployment_notes"):
                st.write(f"**Deployment notes:** {overview['last_deployment_notes']}")
            rollback_command = overview.get("last_deployment_rollback_command")
            if rollback_command:
                with st.expander("Rollback", expanded=False):
                    st.warning(
                        "Review this safe rollback command and run it only through an approved shell gate."
                    )
                    st.code(str(rollback_command), language="bash")
            else:
                st.write("**Rollback:** no safe rollback command recorded")
            st.write(f"**Last build artifact:** {overview['last_build_artifact']}")
            st.write(
                f"**Last build logs:** {str(overview['last_build_logs_summary'])[:500]}"
            )
            log_artifacts = overview.get("last_build_log_artifacts") or []
            if log_artifacts:
                with st.expander("Last build log artifacts", expanded=False):
                    for path in log_artifacts[:8]:
                        st.code(str(path), language=None)
            recent_runs = overview.get("daemon_recent_runs") or []
            if recent_runs:
                with st.expander("Recent daemon runs", expanded=False):
                    for run in recent_runs[:5]:
                        st.write(
                            f"- **{run.get('issue_id', 'unknown')}** — "
                            f"{run.get('status', 'unknown')} "
                            f"updated={run.get('updated_at', 'n/a')}"
                        )
                        if run.get("proof_path"):
                            st.caption(run["proof_path"])
            recent_proofs = overview.get("daemon_recent_proof_of_work") or []
            if recent_proofs:
                with st.expander("Recent proof-of-work artifacts", expanded=False):
                    for proof in recent_proofs[:5]:
                        st.write(
                            f"- **{proof.get('issue', 'unknown')}** — "
                            f"{proof.get('summary', 'No summary')}"
                        )
                        if proof.get("path"):
                            st.caption(proof["path"])

        st.subheader("Live EventBus Feed")
        feed_limit = int(getattr(self.state, "company_feed_limit", 10) or 10)
        bus = getattr(company.orchestrator, "event_bus", None)
        feed_events = bus.get_recent_events(limit=feed_limit) if bus else []
        if feed_events:
            for event in reversed(feed_events):
                self.render_company_event(event)
            if st.button("Load More events", key="company_feed_load_more"):
                self.state.company_feed_limit = feed_limit + 20
                st.rerun()
        else:
            st.caption("No EventBus events have been published yet.")

        m1, m2, m3 = st.columns(3)
        m1.metric("Turns this session", metrics["turns_this_session"])
        m2.metric("Approvals pending", metrics["approvals_pending"])
        m3.metric("Last activity", metrics["last_activity"])

        st.subheader("Skills")
        skills = company.skills_summary()
        recent_skills = skills.get("recently_used") or []
        available_skills = skills.get("available") or []
        if recent_skills:
            st.write("**Recently used**")
            for skill in recent_skills[:5]:
                st.caption(
                    f"{skill.get('scope')}/{skill.get('name')} · "
                    f"{skill.get('title') or skill.get('description') or 'Untitled skill'}"
                )
                if skill.get("path"):
                    st.caption(skill["path"])
        else:
            st.caption("No skills have been used in this repo yet.")
        if available_skills:
            with st.expander("Available skills", expanded=False):
                for skill in available_skills[:10]:
                    st.write(
                        f"- **{skill.get('scope')}/{skill.get('name')}** — "
                        f"{skill.get('description') or skill.get('title') or 'No summary'}"
                    )
                    if skill.get("path"):
                        st.caption(skill["path"])
        else:
            st.caption("No approved skills are available yet.")

        phase = str(metrics["current_phase"])
        st.subheader("Current Phase")
        current_index = COMPANY_PHASES.index(phase) if phase in COMPANY_PHASES else 0
        st.progress(
            (current_index + 1) / len(COMPANY_PHASES),
            text=humanize_company_label(phase),
        )
        phase_cols = st.columns(len(COMPANY_PHASES))
        for index, phase_name in enumerate(COMPANY_PHASES):
            marker = (
                "✅"
                if index < current_index
                else "▶️" if index == current_index else "○"
            )
            phase_cols[index].caption(f"{marker} {humanize_company_label(phase_name)}")

        st.subheader("Recent Deliverables")
        deliverables = company.recent_deliverables()
        if not deliverables:
            st.caption(
                "No deliverables yet. Send a prompt through Company Mode to populate this area."
            )
        for idx, deliverable in enumerate(deliverables):
            title = (
                f"{deliverable['label']} · {humanize_company_label(deliverable['department'])} "
                f"· {humanize_company_label(deliverable['status'])}"
            )
            with st.expander(title, expanded=idx == len(deliverables) - 1):
                files = deliverable.get("files") or []
                if files:
                    st.write("**Files changed**")
                    for fname in files:
                        st.code(str(fname), language=None)
                artifacts = [a for a in deliverable.get("artifact_links") or [] if a]
                if artifacts:
                    st.write("**Artifacts**")
                    for artifact in artifacts:
                        st.code(str(artifact), language=None)
                logs = [a for a in deliverable.get("log_artifacts") or [] if a]
                if logs:
                    st.write("**Log artifacts**")
                    for path in logs:
                        st.code(str(path), language=None)
                if deliverable.get("logs_summary"):
                    st.write("**Logs summary**")
                    st.code(str(deliverable["logs_summary"])[:1200], language=None)
                st.write("**Preview**")
                st.write(
                    truncate_preview(deliverable.get("summary"), 1800)
                    or "No preview available."
                )

        with st.expander(
            "COO Activity (collapsible events, errors, escalations)", expanded=False
        ):
            try:
                coo_status = company.coo_status()
                if coo_status.get("error_count"):
                    st.error(f"Errors: {coo_status.get('error_count')}")
                    st.json(coo_status.get("recent_errors", []))
                if coo_status.get("pending_human_escalations"):
                    st.warning("Pending human escalations")
                    st.json(coo_status.get("pending_human_escalations"))
                st.write("**Recent events**")
                bus = getattr(company.orchestrator, "event_bus", None)
                rich_events = bus.get_recent_events(limit=20) if bus else []
                if rich_events:
                    grouped = {}
                    for event in rich_events:
                        grouped.setdefault(event.event_type, []).append(event)
                    for event_type, events in grouped.items():
                        with st.expander(
                            humanize_company_label(event_type),
                            expanded=event_type
                            in {"approval_required", "project_blocked"},
                        ):
                            for event in events:
                                self.render_company_event(event)
                else:
                    for event in (coo_status.get("recent_events") or [])[-20:]:
                        st.info(str(event))
            except Exception as err:
                st.error(f"COO activity unavailable: {err}")
        with st.expander("Raw company status", expanded=False):
            st.code(company.company_status())

    def do_company_approvals_page(self):
        st.header("Approvals")
        company = self.get_company_for_page()
        if company is None:
            return
        self.do_company_approvals(company, prominent=True)

    def do_company_audit_log_page(self):
        st.header("Audit Log")
        company = self.get_company_for_page()
        if company is None:
            return
        limit = st.slider("Audit events", 1, 50, 15, key="audit_log_limit")
        records = company.audit_records(limit=limit)
        if not records:
            st.caption("No audit events recorded.")
            return
        for record in reversed(records):
            label = " | ".join(
                [
                    str(record.get("timestamp", "")),
                    str(record.get("department", "orchestrator")),
                    str(record.get("event_type", "event")),
                ]
            )
            with st.expander(label, expanded=False):
                st.write(record.get("payload_summary", ""))
                st.json(record.get("metadata", {}))
        with st.expander("Plain-text audit log", expanded=False):
            st.code(company.audit_log(limit=limit))

    def do_knowledge_page(self):
        st.header("Knowledge")
        company = self.get_company_for_page()
        if company is None:
            return
        query = st.text_input(
            "Search Playbooks, Skills, Proposals, and COO Memory",
            key="knowledge_search_query",
            placeholder="Try: playbook, rollout, pending proposal, CEO preference…",
        )
        overview = company.get_knowledge_overview(query=query)
        counts = overview.get("counts", {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Playbooks", counts.get("playbooks", 0))
        c2.metric("Skills", counts.get("skills", 0))
        c3.metric("Pending Proposals", counts.get("pending_proposals", 0))
        c4.metric("COO Memory", counts.get("coo_memory_entries", 0))

        with st.expander("Knowledge EventBus replay", expanded=False):
            knowledge_limit = int(
                getattr(self.state, "knowledge_event_feed_limit", 10) or 10
            )
            bus = getattr(company.orchestrator, "event_bus", None)
            knowledge_events = (
                bus.get_recent_events(
                    filter_by_type={
                        "skill_proposal_updated",
                        "coo_action_taken",
                        "department_event",
                        "lifecycle",
                    },
                    limit=knowledge_limit,
                )
                if bus
                else []
            )
            if knowledge_events:
                for event in reversed(knowledge_events):
                    self.render_company_event(event)
                if st.button(
                    "Load More knowledge events", key="knowledge_feed_load_more"
                ):
                    self.state.knowledge_event_feed_limit = knowledge_limit + 20
                    st.rerun()
            else:
                st.caption("No retained knowledge-related EventBus events yet.")

        recently_injected = overview.get("recently_injected") or []
        st.subheader("Recently Injected")
        if not recently_injected:
            st.caption("No memories or skills have been injected in this session yet.")
        for item in recently_injected[:5]:
            st.caption(
                f"{item.get('type', 'knowledge')} · {item.get('injected_at', 'unknown time')}"
            )
            st.write(item.get("explanation") or "")

        if query:
            st.subheader("Search Results")
            results = overview.get("search_results") or []
            if not results:
                st.caption("No matching knowledge found.")
            for item in results[:25]:
                label = (
                    item.get("title")
                    or item.get("name")
                    or item.get("proposal_id")
                    or item.get("id")
                )
                with st.expander(
                    f"{item.get('type', 'knowledge')} · {label}", expanded=False
                ):
                    st.write(
                        item.get("summary")
                        or item.get("description")
                        or item.get("content")
                        or item.get("rationale")
                        or ""
                    )
                    st.json(item)

        st.subheader("Playbooks")
        playbooks = overview.get("playbooks") or []
        if not playbooks:
            st.caption("No playbook items have been learned yet.")
        for item in playbooks:
            with st.expander(
                f"{item.get('category')} · {item.get('summary', '')[:80]}",
                expanded=False,
            ):
                if item.get("created_at"):
                    st.caption(f"Timestamp: {item['created_at']}")
                st.write(item.get("content") or "")
                if item.get("metadata"):
                    st.json(item.get("metadata"))

        st.subheader("Skills")
        recent = overview.get("recent_skills") or []
        if recent:
            st.write("**Recently used**")
            for skill in recent[:10]:
                st.caption(
                    f"{skill.get('scope')}/{skill.get('name')} · "
                    f"{skill.get('title') or skill.get('description') or 'Untitled'} · "
                    f"{skill.get('last_used_at', 'unknown time')}"
                )
        skills = overview.get("skills") or []
        if not skills:
            st.caption("No approved skills are available yet.")
        for skill in skills:
            with st.expander(
                f"{skill.get('scope')}/{skill.get('name')} · {skill.get('title')}",
                expanded=False,
            ):
                st.write(skill.get("description") or "No description")
                if skill.get("path"):
                    st.code(skill["path"], language=None)
                if skill.get("metadata"):
                    st.json(skill.get("metadata"))

        st.subheader("Skill Proposals")
        proposals = overview.get("proposals") or []
        if not proposals:
            st.caption("No pending or approved skill proposals.")
        for proposal in proposals:
            proposal_id = proposal.get("proposal_id")
            label = f"{proposal.get('status')} · {proposal.get('scope')}/{proposal.get('name')} · {proposal.get('title')}"
            with st.expander(label, expanded=proposal.get("status") == "pending"):
                st.caption(
                    f"Created: {proposal.get('created_at', 'unknown')} · Confidence: {proposal.get('confidence', 'n/a')}"
                )
                st.write(proposal.get("rationale") or "")
                st.code(proposal.get("content") or "", language="markdown")
                if proposal.get("status") == "pending" and proposal_id:
                    col_a, col_b = st.columns(2)
                    if col_a.button(
                        "Approve", key=f"approve_skill_proposal_{proposal_id}"
                    ):
                        company.approve_skill_proposal(proposal_id)
                        st.success(f"Approved {proposal_id}")
                        st.rerun()
                    if col_b.button(
                        "Reject", key=f"reject_skill_proposal_{proposal_id}"
                    ):
                        company.reject_skill_proposal(proposal_id)
                        st.warning(f"Rejected {proposal_id}")
                        st.rerun()

        st.subheader("COO Memory")
        entries = (overview.get("coo_memory") or {}).get("entries") or []
        if not entries:
            st.caption("No COO memory entries yet.")
        for entry in reversed(entries):
            with st.expander(
                f"{entry.get('created_at', 'unknown time')} · {entry.get('type', 'coo_memory')}",
                expanded=False,
            ):
                st.write(entry.get("content") or entry.get("summary") or entry)
                st.json(entry)

    def do_project_memory_page(self):
        st.header("Project Memory")
        project_memory = getattr(self.coder, "project_memory", None)
        if not isinstance(project_memory, ProjectMemory):
            project_memory = ProjectMemory(str(Path(self.coder.root).resolve()))
            project_memory.load()
            self.coder.project_memory = project_memory
        data = project_memory.data
        st.caption(
            "Repo-scoped memory used by Company departments and direct Aider context enrichment."
        )
        if st.button("💾 Persist project memory", key="persist_project_memory"):
            project_memory.persist()
            st.success("Project memory persisted.")
        st.subheader("Playbook")
        st.json(data.get("playbook", {}))
        st.subheader("Observability")
        st.json(data.get("observability", {}))
        with st.expander("Full project memory", expanded=False):
            st.json(data)

    def count_user_turns(self):
        return sum(1 for msg in self.state.messages if msg.get("role") == "user")

    def do_settings_tab(self, location="sidebar"):
        st.subheader("Settings")
        st.caption(
            "Tune models, credentials, agent routing, Discord access, and advanced files "
            "from one shared browser/desktop settings flow. Preview validates changes before save."
        )
        st.info(
            "Settings are repo-local by default. Secrets are written to `.env`, masked in previews, "
            "and applied after the Company session restarts.",
            icon="🔐",
        )

        form_key = f"settings_form_{location}"
        loaded = load_settings_form(
            self.env_path, self.conf_path, self.coder.main_model
        )

        with st.form(form_key, clear_on_submit=False):
            section_tabs = st.tabs(list(SETTINGS_SECTIONS))
            with section_tabs[0]:
                st.write("**Global Aider**")
                st.caption("Defaults for direct Aider chat and code edits.")
                model = st.text_input(
                    "Main model",
                    value=loaded.model,
                    help="The primary model that will answer and edit code.",
                )
                weak_model = st.text_input(
                    "Weak model",
                    value=loaded.weak_model,
                    help="Optional cheaper/faster model for lightweight tasks.",
                )
                editor_model = st.text_input(
                    "Editor model",
                    value=loaded.editor_model,
                    help="Optional model for editor-mode edits.",
                )
                apply_now = st.checkbox(
                    "Use this model for the current session now",
                    value=True,
                    help="Applies the selected Aider model immediately after save.",
                )

            agent_models = {}
            agent_caching = {}
            agent_api_keys = {}
            agent_local_settings = {}
            with section_tabs[1]:
                st.write("**Per-Agent Overrides**")
                st.caption(
                    "Optional model, credential, local endpoint, and prompt-caching overrides "
                    "for COO/Product/UX/Engineering/Reviewer/QA/DevOps."
                )
                for agent_name in COMPANY_AGENT_NAMES:
                    agent_label = company_agent_display_name(agent_name)
                    defaults = loaded.agents.get(agent_name, SettingsAgentForm())
                    with st.expander(f"{agent_label} agent", expanded=False):
                        cols = st.columns([3, 2])
                        agent_models[agent_name] = cols[0].text_input(
                            f"{agent_label} model",
                            value=defaults.model,
                            key=f"{form_key}_{agent_name}_model",
                            placeholder="Use global model",
                        )
                        cache_default = str(defaults.caching or "default").lower()
                        if cache_default not in {"true", "false", "default"}:
                            cache_default = "default"
                        agent_caching[agent_name] = cols[1].selectbox(
                            f"{agent_label} caching",
                            ["default", "true", "false"],
                            index=["default", "true", "false"].index(cache_default),
                            key=f"{form_key}_{agent_name}_caching",
                            help="Prompt caching override for this agent.",
                        )
                        agent_api_keys[agent_name] = st.text_input(
                            f"{agent_label} API key override",
                            value=defaults.api_key,
                            type="password",
                            key=f"{form_key}_{agent_name}_api_key",
                            help="Optional role-specific credential.",
                        )
                        agent_local_settings[agent_name] = st.text_input(
                            f"{agent_label} local endpoint/setting",
                            value=defaults.local,
                            key=f"{form_key}_{agent_name}_local",
                            help="Optional local model endpoint or runtime note.",
                        )

            with section_tabs[2]:
                st.write("**Provider & integration secrets**")
                st.caption(
                    "Saved to repo-local .env. Model provider keys power Aider; the "
                    "Discord token starts the optional chat adapter. Secrets are masked in previews."
                )
                key_cols = st.columns(2)
                openai_key = key_cols[0].text_input(
                    "OpenAI API key",
                    value=loaded.provider_keys.get("OPENAI_API_KEY", ""),
                    type="password",
                )
                anthropic_key = key_cols[1].text_input(
                    "Anthropic API key",
                    value=loaded.provider_keys.get("ANTHROPIC_API_KEY", ""),
                    type="password",
                )
                openrouter_key = key_cols[0].text_input(
                    "OpenRouter API key",
                    value=loaded.provider_keys.get("OPENROUTER_API_KEY", ""),
                    type="password",
                )
                discord_bot_token = key_cols[1].text_input(
                    "Discord bot token",
                    value=loaded.provider_keys.get("DISCORD_BOT_TOKEN", ""),
                    type="password",
                    help="Saved as DISCORD_BOT_TOKEN for the optional Discord chat adapter.",
                )

            with section_tabs[3]:
                st.write("**Advanced (.env + .aider.conf)**")
                provider_keys = st.text_area(
                    "Extra .env settings (one KEY=value per line)",
                    value="",
                    help="Examples: GEMINI_API_KEY=..., AIDER_MODEL_SETTINGS_FILE=...",
                )
                conf_editor = st.text_area(
                    ".aider.conf.yml",
                    value=loaded.conf_text,
                    height=220,
                    help="Raw Aider YAML configuration. Model fields are merged on save.",
                )

            st.write("**Preview before save**")
            st.caption(
                "Click Preview to validate syntax and review exactly what will be written."
            )
            preview_clicked = st.form_submit_button(
                "Preview changes", use_container_width=True
            )
            submitted = st.form_submit_button(
                "🚀 Apply & Restart Company Session",
                type="primary",
                use_container_width=True,
            )

        form = SettingsForm(
            model=model,
            weak_model=weak_model,
            editor_model=editor_model,
            apply_now=apply_now,
            provider_keys={
                "OPENAI_API_KEY": openai_key,
                "ANTHROPIC_API_KEY": anthropic_key,
                "OPENROUTER_API_KEY": openrouter_key,
                "DISCORD_BOT_TOKEN": discord_bot_token,
            },
            extra_env=provider_keys,
            agents={
                name: SettingsAgentForm(
                    model=agent_models.get(name, ""),
                    caching=agent_caching.get(name, "default"),
                    api_key=agent_api_keys.get(name, ""),
                    local=agent_local_settings.get(name, ""),
                )
                for name in COMPANY_AGENT_NAMES
            },
            conf_text=conf_editor,
        )
        preview = build_settings_preview(form)
        if preview_clicked or submitted:
            if preview.valid:
                st.success("Settings validation passed.")
            else:
                st.error("Fix validation errors before applying settings.")
            st.code(preview.render(), language="yaml")

        if submitted:
            if not preview.valid:
                return
            saved = save_settings(self.env_path, self.conf_path, form)
            if saved.valid and apply_now:
                self._apply_model_settings(saved.model_updates)
            shutdown_desktop_company_session(self.coder)
            self.company = None
            st.success(
                "Applied settings and restarted the Company session. New chats use the updated configuration."
            )
            self.info(
                f"Settings saved to `{self.env_path}` and `{self.conf_path}`. Company session restarted."
            )

    def _apply_model_settings(self, model_updates: dict):
        current_model = self.coder.main_model
        model_name = model_updates.get("model") or current_model.name
        weak_model_name = (
            model_updates.get("weak-model") or current_model.weak_model.name
        )
        editor_model_name = (
            model_updates.get("editor-model") or current_model.editor_model.name
        )
        if (
            model_name == current_model.name
            and weak_model_name == current_model.weak_model.name
            and editor_model_name == current_model.editor_model.name
        ):
            return

        next_model = models.Model(
            model_name,
            editor_model=editor_model_name,
            weak_model=weak_model_name,
        )
        models.sanity_check_models(self.coder.commands.io, next_model)
        edit_format = self.coder.edit_format
        if edit_format == current_model.edit_format:
            edit_format = next_model.edit_format
        next_coder = Coder.create(
            from_coder=self.coder,
            main_model=next_model,
            edit_format=edit_format,
            show_announcements=False,
        )
        next_coder.yield_stream = True
        next_coder.stream = True
        next_coder.pretty = False
        self.state.coder = next_coder
        self.coder = next_coder

    def do_recommended_actions(self):
        text = "Aider works best when your code is stored in a git repo.  \n"
        text += f"[See the FAQ for more info]({urls.git})"

        with st.expander("Recommended actions", expanded=True):
            with st.popover("Create a git repo to track changes"):
                st.write(text)
                self.button("Create git repo", key=random.random(), help="?")

            with st.popover("Update your `.gitignore` file"):
                st.write(
                    "It's best to keep aider's internal files out of your git repo."
                )
                self.button(
                    "Add `.aider*` to `.gitignore`", key=random.random(), help="?"
                )

    def do_add_to_chat(self):
        # with st.expander("Add to the chat", expanded=True):
        self.do_add_files()
        self.do_add_web_page()

    def do_add_files(self):
        fnames = st.multiselect(
            "Add files to the chat",
            self.coder.get_all_relative_files(),
            default=self.state.initial_inchat_files,
            placeholder="Files to edit",
            disabled=self.prompt_pending(),
            help=(
                "Only add the files that need to be *edited* for the task you are working"
                " on. Aider will pull in other relevant code to provide context to the LLM."
            ),
        )

        for fname in fnames:
            if fname not in self.coder.get_inchat_relative_files():
                self.coder.add_rel_fname(fname)
                self.info(f"Added {fname} to the chat")

        for fname in self.coder.get_inchat_relative_files():
            if fname not in fnames:
                self.coder.drop_rel_fname(fname)
                self.info(f"Removed {fname} from the chat")

    def do_add_web_page(self):
        with st.popover("Add a web page to the chat"):
            self.do_web()

    def do_add_image(self):
        with st.popover("Add image"):
            st.markdown("Hello World 👋")
            st.file_uploader("Image file", disabled=self.prompt_pending())

    def do_run_shell(self):
        with st.popover("Run shell commands, tests, etc"):
            st.markdown(
                "Run a shell command and optionally share the output with the LLM. This is"
                " a great way to run your program or run tests and have the LLM fix bugs."
            )
            st.text_input("Command:")
            st.radio(
                "Share the command output with the LLM?",
                [
                    "Review the output and decide whether to share",
                    "Automatically share the output on non-zero exit code (ie, if any tests fail)",
                ],
            )
            st.selectbox(
                "Recent commands",
                [
                    "my_app.py --doit",
                    "my_app.py --cleanup",
                ],
                disabled=self.prompt_pending(),
            )

    def do_tokens_and_cost(self):
        with st.expander("Tokens and costs", expanded=True):
            pass

    def do_show_token_usage(self):
        with st.popover("Show token usage"):
            st.write("hi")

    def do_clear_chat_history(self):
        text = "Saves tokens, reduces confusion"
        if self.button("Clear chat history", help=text):
            self.coder.done_messages = []
            self.coder.cur_messages = []
            self.info(
                "Cleared chat history. Now the LLM can't see anything before this line."
            )

    def do_show_metrics(self):
        st.metric("Cost of last message send & reply", "$0.0019", help="foo")
        st.metric("Cost to send next message", "$0.0013", help="foo")
        st.metric("Total cost this session", "$0.22")

    def do_git(self):
        with st.expander("Git", expanded=False):
            # st.button("Show last diff")
            # st.button("Undo last commit")
            self.button("Commit any pending changes")
            with st.popover("Run git command"):
                st.markdown("## Run git command")
                st.text_input("git", value="git ")
                self.button("Run")
                st.selectbox(
                    "Recent git commands",
                    [
                        "git checkout -b experiment",
                        "git stash",
                    ],
                    disabled=self.prompt_pending(),
                )

    def do_recent_msgs(self):
        if not self.recent_msgs_empty:
            self.recent_msgs_empty = st.empty()

        if self.prompt_pending():
            self.recent_msgs_empty.empty()
            self.state.recent_msgs_num += 1

        with self.recent_msgs_empty:
            self.old_prompt = st.selectbox(
                "Resend a recent chat message",
                self.state.input_history,
                placeholder="Choose a recent chat message",
                # label_visibility="collapsed",
                index=None,
                key=f"recent_msgs_{self.state.recent_msgs_num}",
                disabled=self.prompt_pending(),
            )
            if self.old_prompt:
                self.prompt = self.old_prompt

    def do_messages_container(self):
        self.messages = st.container()

        # stuff a bunch of vertical whitespace at the top
        # to get all the chat text to the bottom
        # self.messages.container(height=300, border=False)

        with self.messages:
            for msg in self.state.messages:
                role = msg["role"]

                if role == "edit":
                    self.show_edit_info(msg)
                elif role == "info":
                    st.info(msg["content"])
                elif role == "text":
                    text = msg["content"]
                    line = text.splitlines()[0]
                    with self.messages.expander(line):
                        st.text(text)
                elif role in ("user", "assistant"):
                    with st.chat_message(role):
                        st.write(msg["content"])
                        # self.cost()
                else:
                    st.dict(msg)

    def initialize_state(self):
        messages = [
            dict(role="info", content=self.announce()),
            dict(role="assistant", content="How can I help you?"),
        ]

        self.state.init("messages", messages)
        self.state.init("last_aider_commit_hash", self.coder.last_aider_commit_hash)
        self.state.init("last_undone_commit_hash")
        self.state.init("recent_msgs_num", 0)
        self.state.init("web_content_num", 0)
        self.state.init("prompt")
        self.state.init("scraper")
        self.state.init("show_settings", False)
        self.state.init("company_enabled", False)
        self.state.init("company_route", "Auto")
        self.state.init("company_auto_refresh", True)
        self.state.init("company_bypass_next", False)
        self.state.init("company_event_version", 0)
        self.state.init("agent_messages", {})
        self.state.init("agent_chat_target", "Direct Aider")
        self.state.init("active_agent_chat", "Direct Aider")

        self.state.init("initial_inchat_files", self.coder.get_inchat_relative_files())
        root = Path(self.coder.root)
        self.state.init("env_path", root / ".env")
        self.state.init("conf_path", root / ".aider.conf.yml")

        if "input_history" not in self.state.keys:
            input_history = list(self.coder.io.get_input_history())
            seen = set()
            input_history = [x for x in input_history if not (x in seen or seen.add(x))]
            self.state.input_history = input_history
            self.state.keys.add("input_history")

    def button(self, args, **kwargs):
        "Create a button, disabled if prompt pending"

        # Force everything to be disabled if there is a prompt pending
        if self.prompt_pending():
            kwargs["disabled"] = True

        return st.button(args, **kwargs)

    def __init__(self):
        self.state = get_state()
        self.state.init("coder", get_coder())
        self.coder = self.state.coder

        # Force the coder to cooperate, regardless of cmd line args
        self.coder.yield_stream = True
        self.coder.stream = True
        self.coder.pretty = False

        self.initialize_state()
        self.env_path = self.state.env_path
        self.conf_path = self.state.conf_path
        self.company = None

        self.do_main_tabs()
        self.do_sidebar()

        input_target = st.selectbox(
            "Chat input target",
            AGENT_CHAT_TARGETS,
            index=(
                AGENT_CHAT_TARGETS.index(self.state.agent_chat_target)
                if self.state.agent_chat_target in AGENT_CHAT_TARGETS
                else 0
            ),
            key="global_chat_input_target",
            format_func=lambda target: f"{AGENT_ICONS.get(target, '🤖')} {target}",
            label_visibility="collapsed",
            disabled=self.prompt_pending(),
        )
        self.state.active_agent_chat = input_target
        chat_placeholder = (
            "Say something (direct Aider chat)"
            if input_target == "Direct Aider"
            else f"Say something for {input_target}"
        )
        user_inp = st.chat_input(chat_placeholder)
        if user_inp:
            self.state.agent_chat_target = input_target
            self.prompt = user_inp

        if self.prompt_pending():
            self.process_chat()

        if not self.prompt:
            return

        self.state.prompt = self.prompt

        if self.prompt_as == "user":
            self.coder.io.add_to_input_history(self.prompt)

        self.state.input_history.append(self.prompt)

        target = self.state.agent_chat_target
        if target != "Direct Aider":
            self.state.agent_messages.setdefault(target, []).append(
                {"role": "user", "content": self.prompt}
            )
        elif self.prompt_as:
            self.state.messages.append({"role": self.prompt_as, "content": self.prompt})
        if self.prompt_as == "user" and target == "Direct Aider":
            with self.messages.chat_message("user"):
                st.write(self.prompt)
        elif self.prompt_as == "text":
            line = self.prompt.splitlines()[0]
            line += "??"
            with self.messages.expander(line):
                st.text(self.prompt)

        # re-render the UI for the prompt_pending state
        st.rerun()

    def get_company(self):
        if self.company is None:
            self.company = get_desktop_company_session(self.coder)
        return self.company

    def prompt_pending(self):
        return self.state.prompt is not None

    def cost(self):
        cost = random.random() * 0.003 + 0.001
        st.caption(f"${cost:0.4f}")

    def process_chat(self):
        prompt = self.state.prompt
        target = self.state.agent_chat_target
        self.state.prompt = None

        if target != "Direct Aider":
            self.process_agent_chat(target, prompt)
            return

        if self.state.company_enabled and not self.state.company_bypass_next:
            self.process_company_chat(prompt)
            return

        if self.state.company_bypass_next:
            self.state.company_bypass_next = False

        # This duplicates logic from within Coder
        self.num_reflections = 0
        self.max_reflections = 3

        while prompt:
            with self.messages.chat_message("assistant"):
                res = st.write_stream(self.coder.run_stream(prompt))
                self.state.messages.append({"role": "assistant", "content": res})
                # self.cost()

            prompt = None
            if self.coder.reflected_message:
                if self.num_reflections < self.max_reflections:
                    self.num_reflections += 1
                    self.info(self.coder.reflected_message)
                    prompt = self.coder.reflected_message

        with self.messages:
            edit = dict(
                role="edit",
                fnames=self.coder.aider_edited_files,
            )
            if self.state.last_aider_commit_hash != self.coder.last_aider_commit_hash:
                edit["commit_hash"] = self.coder.last_aider_commit_hash
                edit["commit_message"] = self.coder.last_aider_commit_message
                commits = f"{self.coder.last_aider_commit_hash}~1"
                diff = self.coder.repo.diff_commits(
                    self.coder.pretty,
                    commits,
                    self.coder.last_aider_commit_hash,
                )
                edit["diff"] = diff
                self.state.last_aider_commit_hash = self.coder.last_aider_commit_hash

            self.state.messages.append(edit)
            self.show_edit_info(edit)

        # re-render the UI for the non-prompt_pending state
        st.rerun()

    def process_agent_chat(self, target: str, prompt: str):
        if target == "Company Workflow":
            self.process_company_chat(prompt, target=target)
            return

        agent_name = target.lower()
        try:
            future = self.get_company().chat_with_agent(agent_name, prompt)
            result = future.result()
            content = result.get("summary") if isinstance(result, dict) else str(result)
        except Exception as err:
            content = f"{target} agent failed: {err}"
            st.error(content)
        self.state.agent_messages.setdefault(target, []).append(
            {"role": "assistant", "content": content or "No response returned."}
        )
        self.state.agent_chat_target = "Direct Aider"
        st.rerun()

    def process_company_chat(self, prompt, target: str = "Company Workflow"):
        route = self.state.company_route
        if route == "Prototype":
            self.get_company().start_prototype(prompt)
        elif route == "Engineering":
            self.get_company().run_instruction(prompt)
        else:
            self.get_company().run_auto(prompt)

        with self.messages.chat_message("assistant"):
            st.write(
                f"Queued `{route}` company workflow. Use the Company sidebar to "
                "refresh status, review approvals, open the dashboard, or inspect "
                "the audit log."
            )
        response_message = (
            f"Queued `{route}` company workflow for background processing. "
            "Check the Company sidebar for progress and approvals."
        )
        if target == "Company Workflow":
            self.state.agent_messages.setdefault(target, []).append(
                {"role": "assistant", "content": response_message}
            )
        else:
            self.state.messages.append(
                {"role": "assistant", "content": response_message}
            )
        st.rerun()

    def info(self, message, echo=True):
        info = dict(role="info", content=message)
        self.state.messages.append(info)

        # We will render the tail of the messages array after this call
        if echo:
            self.messages.info(message)

    def do_web(self):
        st.markdown("Add the text content of a web page to the chat")

        if not self.web_content_empty:
            self.web_content_empty = st.empty()

        if self.prompt_pending():
            self.web_content_empty.empty()
            self.state.web_content_num += 1

        with self.web_content_empty:
            self.web_content = st.text_input(
                "URL",
                placeholder="https://...",
                key=f"web_content_{self.state.web_content_num}",
            )

        if not self.web_content:
            return

        url = self.web_content

        if not self.state.scraper:
            self.scraper = Scraper(
                print_error=self.info, playwright_available=has_playwright()
            )

        content = self.scraper.scrape(url) or ""
        if content.strip():
            content = f"{url}\n\n" + content
            self.prompt = content
            self.prompt_as = "text"
        else:
            self.info(f"No web content found for `{url}`.")
            self.web_content = None

    def do_undo(self, commit_hash):
        self.last_undo_empty.empty()

        if (
            self.state.last_aider_commit_hash != commit_hash
            or self.coder.last_aider_commit_hash != commit_hash
        ):
            self.info(f"Commit `{commit_hash}` is not the latest commit.")
            return

        self.coder.commands.io.get_captured_lines()
        reply = self.coder.commands.cmd_undo(None)
        lines = self.coder.commands.io.get_captured_lines()

        lines = "\n".join(lines)
        lines = lines.splitlines()
        lines = "  \n".join(lines)
        self.info(lines, echo=False)

        self.state.last_undone_commit_hash = commit_hash

        if reply:
            self.prompt_as = None
            self.prompt = reply


def gui_main():
    st.set_page_config(
        layout="wide",
        page_title="Aider",
        page_icon=urls.favicon,
        menu_items={
            "Get Help": urls.website,
            "Report a bug": "https://github.com/Aider-AI/aider/issues",
            "About": "# Aider\nAI pair programming in your browser.",
        },
    )

    # config_options = st.config._config_options
    # for key, value in config_options.items():
    #    print(f"{key}: {value.value}")

    GUI()


if __name__ == "__main__":
    status = gui_main()
    sys.exit(status)
