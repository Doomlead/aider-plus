#!/usr/bin/env python
"""Zero-dependency native Tkinter desktop launcher for Aider Plus.

The desktop app intentionally uses only Tkinter and the Python standard library
for its windowing layer. It reuses Aider's normal coder bootstrap and Company
Mode orchestration without starting Streamlit, pywebview, WebView2, or a browser.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import logging
import queue
import threading
import tkinter as tk
import uuid
from collections import deque
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

from aider import models
from aider.agent.loop import AgentLoopConfig
from aider.coders import Coder
from aider.company.agent_factory import build_company_agent_loops
from aider.company.audit import AuditLogViewer
from aider.company.config import apply_agent_model_overrides_from_env
from aider.company.coo import NanobotCOO
from aider.company.departments.devops import DevOpsDepartment
from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.product import ProductDepartment
from aider.company.departments.qa import QADepartment
from aider.company.departments.ux import UXDepartment
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.project import Project
from aider.company.schemas import CompanyTask
from aider.main import main as cli_main
from aider.memory import ConversationMemory, ProjectMemory, consolidate_conversation
from aider.settings import (
    COMPANY_AGENT_NAMES,
    agent_api_key_env_name,
    agent_caching_env_name,
    agent_local_env_name,
    agent_model_env_name,
    apply_env_updates,
    collect_agent_env_updates,
    collect_provider_key_updates,
    parse_conf_text,
    read_env_values,
    write_conf_text,
    write_env_updates,
)

logger = logging.getLogger(__name__)
_COMPANY_SESSIONS: dict[str, "DesktopCompanySession"] = {}
_COMPANY_SESSIONS_LOCK = threading.Lock()

AGENT_DISPLAY_NAMES = {
    "coo": "COO",
    "ux": "UX",
    "qa": "QA",
    "devops": "DevOps",
}


def company_agent_display_name(agent_name: str) -> str:
    """Return the user-facing label for a Company agent name."""
    normalized = str(agent_name or "").strip().lower()
    return AGENT_DISPLAY_NAMES.get(normalized, normalized.replace("_", " ").title())


def company_label(value: str) -> str:
    """Return a UI label while preserving common Company acronyms."""
    text = str(value or "").replace("_", " ").strip()
    if not text:
        return ""
    words = [company_agent_display_name(word) for word in text.split()]
    return " ".join(words)


class DesktopCompanySession:
    """Desktop façade over the same Company workflow used by integrations."""

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
        self.devops = DevOpsDepartment(
            project_memory=project_memory,
            agent_loop=agent_loops["devops"],
        )
        self.orchestrator = CompanyOrchestrator(
            project_memory=project_memory,
            company_config=company_config,
        )
        self.orchestrator.active_project = self.active_project
        for department in (
            self.product,
            self.ux,
            self.engineering,
            self.qa,
            self.devops,
        ):
            self.orchestrator.register(department)
        self.coo = NanobotCOO(
            orchestrator=self.orchestrator,
            agent_loop=agent_loops["coo"],
        )
        self.coo.bus.on_event(self._record_coo_bus_event)
        for department in (
            self.product,
            self.ux,
            self.engineering,
            self.qa,
            self.devops,
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

    async def _record_company_message(self, message):
        with self.event_lock:
            self.events.append(message)
            self.event_queue.append(message)
            self.event_version += 1

    async def _record_coo_bus_event(self, event):
        with self.event_lock:
            self.events.append(event)
            self.event_queue.append(event)
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
            "status": deliverable.status,
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

    def coo_status(self) -> dict[str, Any]:
        if self.coo is None:
            return {}
        session_id = f"desktop:{self.repo_path}"
        future = asyncio.run_coroutine_threadsafe(
            self.coo.get_session_status(session_id), self.loop
        )
        return future.result(timeout=5)

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
            ("deploy_result", "Deployment"),
        ):
            deliverable = getattr(project, attr, None)
            if not deliverable:
                continue
            metadata = getattr(deliverable, "metadata", {}) or {}
            files = metadata.get("files") or metadata.get("files_changed") or []
            deliverables.append(
                {
                    "label": label,
                    "department": getattr(deliverable, "department", "company"),
                    "status": getattr(deliverable, "status", "unknown"),
                    "summary": getattr(deliverable, "payload", ""),
                    "files": files if isinstance(files, list) else [files],
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


def get_desktop_company_session(coder: Coder) -> DesktopCompanySession:
    repo_path = str(Path(coder.root).resolve())
    with _COMPANY_SESSIONS_LOCK:
        session = _COMPANY_SESSIONS.get(repo_path)
        if session is not None and not session._shutdown:
            return session
        session = DesktopCompanySession(coder)
        _COMPANY_SESSIONS[repo_path] = session
        return session


APP_TITLE = "Aider Plus"
WINDOW_SIZE = "1200x800"
MIN_WINDOW_SIZE = (1000, 700)
POLL_INTERVAL_MS = 350

DESKTOP_TAB_GUIDE = {
    "Chat": (
        "Talk to classic Aider, send work through the full Company workflow, or open a "
        "dedicated conversation with one Company agent. The text box at the bottom sends "
        "to whichever chat sub-tab is selected."
    ),
    "Settings": (
        "Save repo-local model, provider, per-agent, and advanced Aider configuration. "
        "Settings are written to .env and .aider.conf.yml in the current repository."
    ),
    "Company Dashboard": (
        "Monitor Company Mode progress, active runs, recent deliverables, and COO routing "
        "or recovery activity."
    ),
    "Approvals": (
        "Review human gates raised by Product, UX, Engineering, QA, DevOps, or the COO and "
        "approve, reject, or request changes with feedback."
    ),
    "Audit": (
        "Read the repo-scoped audit log for Company workflow events, decisions, approvals, "
        "and generated artifacts."
    ),
    "Guide": (
        "Explains every desktop tab, chat target, settings field, dashboard field, approval "
        "field, and audit field."
    ),
}

CHAT_TARGET_GUIDE = {
    "Direct Aider": "Sends the prompt straight to the normal Aider coding session.",
    "Company Workflow": (
        "Routes the prompt through COO-led Product → UX → Engineering → QA → DevOps orchestration."
    ),
    "COO": "Ask the COO personal assistant to brief the CEO, remember preferences, or delegate work.",
    "Product": "Talks directly with the Product agent for requirements, PRDs, and ambiguity checks.",
    "UX": "Talks directly with the UX agent for design specs, screens, states, and accessibility.",
    "Engineering": "Talks directly with the Engineering agent for implementation plans and code changes.",
    "Reviewer": "Talks directly with the reviewer agent for implementation review and quality checks.",
    "QA": "Talks directly with the QA agent for test plans, test execution guidance, and validation.",
    "DevOps": "Talks directly with the DevOps agent for release, deployment, and operational guidance.",
}

DESKTOP_CHROME_GUIDE = (
    ("⚙ Settings", "Header shortcut that opens the Settings tab."),
    ("Repo", "Header label showing the repository connected to this desktop session."),
    ("Status bar", "Bottom label showing startup, busy, ready, or error state."),
)

CHAT_FIELD_GUIDE = (
    (
        "Chat sub-tab",
        "Selects which transcript and agent/workflow receives the next message.",
    ),
    (
        "Transcript",
        "Read-only conversation history for the selected Direct Aider, workflow, or agent tab.",
    ),
    ("Message box", "Type the prompt to send to the selected chat sub-tab."),
    ("Send", "Submits the message box contents to the selected chat sub-tab."),
)

SETTINGS_FIELD_GUIDE = (
    (
        "Main model",
        "Primary model used by direct Aider chat for answers and code edits.",
    ),
    ("Weak model", "Optional cheaper/faster model used for lightweight helper work."),
    ("Editor model", "Optional model used for editor-mode file edits."),
    (
        "Apply model to the current desktop session now",
        "Immediately rebuilds the active desktop coder with the selected Aider models after saving.",
    ),
    ("OpenAI API key", "Repo-local OPENAI_API_KEY value saved to .env."),
    ("Anthropic API key", "Repo-local ANTHROPIC_API_KEY value saved to .env."),
    ("OpenRouter API key", "Repo-local OPENROUTER_API_KEY value saved to .env."),
    (
        "Other provider keys/env",
        "Additional KEY=value lines saved to .env, such as GEMINI_API_KEY or local provider settings.",
    ),
    (
        "Agent",
        "The Company role being configured: COO, Product, UX, Engineering, Reviewer, QA, or DevOps.",
    ),
    (
        "Model override",
        "Optional AIDER_COMPANY_MODEL_<AGENT> model name for that one agent; blank uses defaults.",
    ),
    (
        "Prompt caching",
        "Turns request-level prompt caching on or off for that one agent when supported by the provider.",
    ),
    (
        "API key override",
        "Optional AIDER_COMPANY_API_KEY_<AGENT> secret for a role-specific provider credential.",
    ),
    (
        "Local endpoint/setting",
        "Optional AIDER_COMPANY_LOCAL_<AGENT> value for a local model endpoint or runtime note.",
    ),
    (
        "Advanced .aider.conf.yml",
        "Raw Aider YAML configuration; model fields above are merged into this file on save.",
    ),
    ("Reload Settings", "Reloads values from .env and .aider.conf.yml into the form."),
    (
        "Save Settings",
        "Writes form values, applies environment updates, and restarts Company sessions.",
    ),
)

DASHBOARD_FIELD_GUIDE = (
    (
        "Refresh Dashboard",
        "Manually reloads metrics, Company status, deliverables, and COO activity.",
    ),
    ("Phase", "Current project workflow phase reported by Company Mode."),
    ("Pending Approvals", "Number of open approval gates requiring user action."),
    ("Active Runs", "Number of background Company tasks still running."),
    ("Chat Turns", "Number of prompts sent during this desktop session."),
    (
        "Company Status",
        "Plain-text orchestrator status for departments, queues, and project state.",
    ),
    (
        "Recent Deliverables",
        "Latest PRDs, design specs, engineering output, QA results, and deployment notes.",
    ),
    (
        "COO Activity",
        "CEO/COO action, route, active department, recent events, errors, memory, and recovery suggestions.",
    ),
)

APPROVALS_FIELD_GUIDE = (
    ("Refresh Approvals", "Reloads pending approval gates from Company state."),
    ("Task", "Identifier for the gated task or approval request."),
    ("Department", "Agent or department that created the approval gate."),
    (
        "Gate",
        "Type of approval being requested, such as PRD approval or human escalation.",
    ),
    ("Status", "Current approval state, usually pending until acted on."),
    (
        "Approval Details",
        "Full JSON payload for the selected approval, including artifact preview and metadata.",
    ),
    (
        "Feedback",
        "Optional message sent with approve/reject, required when requesting changes.",
    ),
    ("Approve", "Accepts the selected gate and lets the workflow continue."),
    (
        "Request Changes",
        "Rejects the gate as a revision request and sends the feedback back to the workflow.",
    ),
    ("Reject", "Rejects the selected gate and records the feedback/reason."),
)

AUDIT_FIELD_GUIDE = (
    ("Refresh Audit", "Reloads the latest audit records from project memory."),
    (
        "Audit log",
        "Read-only chronological record of Company workflow events and decisions.",
    ),
)


class AiderPlusDesktop:
    """Pure Tkinter desktop shell around Aider Company Mode."""

    def __init__(self, argv: list[str] | None = None):
        self.argv = _strip_desktop_args(argv)
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(*MIN_WINDOW_SIZE)

        self.coder: Coder | None = None
        self.company: DesktopCompanySession | None = None
        self.turns_this_session = 0
        self.selected_approval_id: str | None = None
        self._future_labels: dict[concurrent.futures.Future, str] = {}
        self._ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._closing = False
        self.env_path: Path | None = None
        self.conf_path: Path | None = None
        self.model_vars: dict[str, tk.StringVar] = {}
        self.api_key_vars: dict[str, tk.StringVar] = {}
        self.agent_model_vars: dict[str, tk.StringVar] = {}
        self.agent_caching_vars: dict[str, tk.BooleanVar] = {}
        self.agent_api_key_vars: dict[str, tk.StringVar] = {}
        self.agent_local_vars: dict[str, tk.StringVar] = {}
        self.chat_transcripts: dict[str, scrolledtext.ScrolledText] = {}

        self._setup_style()
        self._setup_ui()
        self._set_busy(True, "Starting Aider backend…")
        self._init_backend()
        self.root.after(POLL_INTERVAL_MS, self._poll_background)

    def _setup_style(self):
        style = ttk.Style(self.root)
        preferred_themes = ["clam", "vista", "aqua", "default"]
        available = set(style.theme_names())
        for theme in preferred_themes:
            if theme in available:
                style.theme_use(theme)
                break
        style.configure("Header.TLabel", font=("TkDefaultFont", 14, "bold"))
        style.configure("Metric.TLabel", font=("TkDefaultFont", 11, "bold"))
        style.configure("Status.TLabel", padding=(8, 4))
        style.configure("Accent.TButton", padding=(10, 6))

    def _setup_ui(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text=APP_TITLE, style="Header.TLabel").pack(side="left")
        ttk.Button(header, text="⚙ Settings", command=self.open_settings).pack(
            side="left", padx=(12, 0)
        )
        self.repo_label = ttk.Label(header, text="Initializing…")
        self.repo_label.pack(side="right")

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)

        self.chat_frame = ttk.Frame(self.notebook, padding=8)
        self.dashboard_frame = ttk.Frame(self.notebook, padding=8)
        self.approvals_frame = ttk.Frame(self.notebook, padding=8)
        self.audit_frame = ttk.Frame(self.notebook, padding=8)
        self.settings_frame = ttk.Frame(self.notebook, padding=8)
        self.guide_frame = ttk.Frame(self.notebook, padding=8)

        self.notebook.add(self.chat_frame, text="Chat")
        self.notebook.add(self.settings_frame, text="Settings")
        self.notebook.add(self.dashboard_frame, text="Company Dashboard")
        self.notebook.add(self.approvals_frame, text="Approvals")
        self.notebook.add(self.audit_frame, text="Audit")
        self.notebook.add(self.guide_frame, text="Guide")

        self._build_chat_tab()
        self._build_settings_tab()
        self._build_dashboard_tab()
        self._build_approvals_tab()
        self._build_audit_tab()
        self._build_guide_tab()

        self.status_label = ttk.Label(
            outer, text="Ready", anchor="w", style="Status.TLabel"
        )
        self.status_label.pack(fill="x", pady=(8, 0))

    def _add_tab_description(self, parent, tab_name: str):
        ttk.Label(
            parent,
            text=DESKTOP_TAB_GUIDE[tab_name],
            wraplength=960,
            justify="left",
        ).pack(fill="x", pady=(0, 8))

    def _add_field_guide(self, parent, title: str, rows):
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="x", pady=(0, 8))
        for row, (field, description) in enumerate(rows):
            ttk.Label(frame, text=field, style="Metric.TLabel").grid(
                row=row, column=0, sticky="nw", pady=2
            )
            ttk.Label(frame, text=description, wraplength=760, justify="left").grid(
                row=row, column=1, sticky="ew", padx=(12, 0), pady=2
            )
        frame.columnconfigure(1, weight=1)
        return frame

    def _build_chat_tab(self):
        self.chat_notebook = ttk.Notebook(self.chat_frame)
        self.chat_notebook.pack(fill="both", expand=True)
        self.chat_targets = [
            "Direct Aider",
            "Company Workflow",
            *[company_agent_display_name(name) for name in COMPANY_AGENT_NAMES],
        ]
        for target in self.chat_targets:
            frame = ttk.Frame(self.chat_notebook, padding=4)
            text = scrolledtext.ScrolledText(
                frame,
                wrap=tk.WORD,
                state="disabled",
                font=("TkDefaultFont", 10),
                padx=10,
                pady=10,
            )
            text.pack(fill="both", expand=True)
            for tag, foreground, font in (
                ("user", "#155EEF", ("TkDefaultFont", 10, "bold")),
                ("aider", "#047857", ("TkDefaultFont", 10, "bold")),
                ("system", "#6B7280", ("TkDefaultFont", 10, "italic")),
                ("error", "#B42318", ("TkDefaultFont", 10, "bold")),
            ):
                text.tag_configure(tag, foreground=foreground, font=font)
            text.tag_configure("code", font=("TkFixedFont", 10), background="#F3F4F6")
            self.chat_transcripts[target] = text
            self.chat_notebook.add(frame, text=target)
        self.chat_text = self.chat_transcripts["Company Workflow"]

        input_frame = ttk.Frame(self.chat_frame)
        input_frame.pack(fill="x", pady=(8, 0))

        self.chat_entry = ttk.Entry(input_frame)
        self.chat_entry.pack(side="left", fill="x", expand=True)
        self.chat_entry.bind("<Return>", lambda _event: self.send_chat_message())

        self.send_button = ttk.Button(
            input_frame,
            text="Send",
            command=self.send_chat_message,
            style="Accent.TButton",
        )
        self.send_button.pack(side="right", padx=(8, 0))

        for target in self.chat_targets:
            self._append_chat(
                "System",
                "Backend is starting. Send a request once the status bar says Ready.",
                tag="system",
                target=target,
            )

    def _build_settings_tab(self):
        canvas = tk.Canvas(self.settings_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            self.settings_frame, orient="vertical", command=canvas.yview
        )
        content = ttk.Frame(canvas)
        content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        model_frame = ttk.LabelFrame(content, text="Aider Models", padding=8)
        model_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(
            model_frame,
            text="These fields control the default direct Aider model stack.",
            wraplength=820,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        for row, (key, label, help_text) in enumerate(
            (
                ("model", "Main model", "Primary model for answers and edits."),
                (
                    "weak-model",
                    "Weak model",
                    "Optional cheaper/faster model for lightweight tasks.",
                ),
                (
                    "editor-model",
                    "Editor model",
                    "Optional model for editor-mode edits.",
                ),
            ),
            start=1,
        ):
            ttk.Label(model_frame, text=label).grid(
                row=row, column=0, sticky="w", pady=2
            )
            var = tk.StringVar()
            self.model_vars[key] = var
            ttk.Entry(model_frame, textvariable=var, width=45).grid(
                row=row, column=1, sticky="ew", padx=8, pady=2
            )
            ttk.Label(model_frame, text=help_text).grid(
                row=row, column=2, sticky="w", pady=2
            )
        self.apply_model_now_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            model_frame,
            text="Apply model to the current desktop session now",
            variable=self.apply_model_now_var,
        ).grid(row=4, column=1, sticky="w", pady=(6, 0))
        model_frame.columnconfigure(1, weight=1)

        api_frame = ttk.LabelFrame(content, text="API Keys and Environment", padding=8)
        api_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(
            api_frame,
            text="Provider credentials and extra KEY=value environment settings saved to .env.",
            wraplength=820,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        for row, (key, label) in enumerate(
            (
                ("OPENAI_API_KEY", "OpenAI API key"),
                ("ANTHROPIC_API_KEY", "Anthropic API key"),
                ("OPENROUTER_API_KEY", "OpenRouter API key"),
            ),
            start=1,
        ):
            ttk.Label(api_frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self.api_key_vars[key] = var
            ttk.Entry(api_frame, textvariable=var, show="•", width=58).grid(
                row=row, column=1, sticky="ew", padx=8, pady=2
            )
        ttk.Label(
            api_frame, text="Other provider keys/env (KEY=value, one per line)"
        ).grid(row=4, column=0, sticky="nw", pady=2)
        self.provider_keys_text = scrolledtext.ScrolledText(
            api_frame, wrap=tk.WORD, height=4
        )
        self.provider_keys_text.grid(row=4, column=1, sticky="ew", padx=8, pady=2)
        api_frame.columnconfigure(1, weight=1)

        agents_frame = ttk.LabelFrame(
            content, text="Company Agent Models and Caching", padding=8
        )
        agents_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(
            agents_frame,
            text="Each row configures one Company agent. Blank overrides fall back to shared defaults.",
            wraplength=980,
            justify="left",
        ).grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 6))
        ttk.Label(agents_frame, text="Agent").grid(row=1, column=0, sticky="w")
        ttk.Label(agents_frame, text="Model override").grid(row=1, column=1, sticky="w")
        ttk.Label(agents_frame, text="Prompt caching").grid(row=1, column=2, sticky="w")
        for row, agent_name in enumerate(COMPANY_AGENT_NAMES, start=2):
            ttk.Label(agents_frame, text=company_agent_display_name(agent_name)).grid(
                row=row, column=0, sticky="w", pady=2
            )
            model_var = tk.StringVar()
            self.agent_model_vars[agent_name] = model_var
            ttk.Entry(agents_frame, textvariable=model_var, width=40).grid(
                row=row, column=1, sticky="ew", padx=8, pady=2
            )
            caching_var = tk.BooleanVar(value=True)
            self.agent_caching_vars[agent_name] = caching_var
            ttk.Checkbutton(agents_frame, text="Enabled", variable=caching_var).grid(
                row=row, column=2, sticky="w", pady=2
            )
            api_var = tk.StringVar()
            self.agent_api_key_vars[agent_name] = api_var
            ttk.Entry(agents_frame, textvariable=api_var, show="•", width=28).grid(
                row=row, column=3, sticky="ew", padx=8, pady=2
            )
            local_var = tk.StringVar()
            self.agent_local_vars[agent_name] = local_var
            ttk.Entry(agents_frame, textvariable=local_var, width=28).grid(
                row=row, column=4, sticky="ew", padx=8, pady=2
            )
        ttk.Label(agents_frame, text="API key override").grid(
            row=0, column=3, sticky="w"
        )
        ttk.Label(agents_frame, text="Local endpoint/setting").grid(
            row=0, column=4, sticky="w"
        )
        agents_frame.columnconfigure(1, weight=1)
        agents_frame.columnconfigure(3, weight=1)
        agents_frame.columnconfigure(4, weight=1)

        conf_frame = ttk.LabelFrame(content, text="Advanced .aider.conf.yml", padding=8)
        conf_frame.pack(fill="both", expand=True, pady=(0, 8))
        ttk.Label(
            conf_frame,
            text="Edit raw Aider YAML. Saving merges the model fields above into this text.",
            wraplength=820,
            justify="left",
        ).pack(fill="x", pady=(0, 4))
        self.conf_text = scrolledtext.ScrolledText(conf_frame, wrap=tk.WORD, height=10)
        self.conf_text.pack(fill="both", expand=True)

        actions = ttk.Frame(content)
        actions.pack(fill="x")
        ttk.Button(
            actions, text="Reload Settings", command=self.refresh_settings_fields
        ).pack(side="left")
        ttk.Button(
            actions,
            text="Save Settings",
            command=self.save_settings,
            style="Accent.TButton",
        ).pack(side="right")

    def open_settings(self):
        self.notebook.select(self.settings_frame)

    def _build_dashboard_tab(self):
        toolbar = ttk.Frame(self.dashboard_frame)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(
            toolbar, text="Refresh Dashboard", command=self.refresh_dashboard
        ).pack(side="right")

        metrics = ttk.Frame(self.dashboard_frame)
        metrics.pack(fill="x", pady=(0, 8))
        self.metric_labels: dict[str, ttk.Label] = {}
        for idx, (key, label) in enumerate(
            (
                ("current_phase", "Phase"),
                ("approvals_pending", "Pending Approvals"),
                ("active_runs", "Active Runs"),
                ("turns_this_session", "Chat Turns"),
            )
        ):
            card = ttk.LabelFrame(metrics, text=label, padding=8)
            card.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 6, 0))
            metrics.columnconfigure(idx, weight=1)
            value = ttk.Label(card, text="—", style="Metric.TLabel")
            value.pack(anchor="w")
            self.metric_labels[key] = value

        panes = ttk.PanedWindow(self.dashboard_frame, orient=tk.VERTICAL)
        panes.pack(fill="both", expand=True)

        status_frame = ttk.LabelFrame(panes, text="Company Status", padding=4)
        self.dashboard_text = scrolledtext.ScrolledText(
            status_frame, wrap=tk.WORD, height=12
        )
        self.dashboard_text.pack(fill="both", expand=True)
        panes.add(status_frame, weight=3)

        deliverables_frame = ttk.LabelFrame(
            panes, text="Recent Deliverables", padding=4
        )
        self.deliverables_text = scrolledtext.ScrolledText(
            deliverables_frame, wrap=tk.WORD, height=8
        )
        self.deliverables_text.pack(fill="both", expand=True)
        panes.add(deliverables_frame, weight=2)

        coo_frame = ttk.LabelFrame(panes, text="CEO/COO Activity", padding=4)
        self.coo_status_text = scrolledtext.ScrolledText(
            coo_frame, wrap=tk.WORD, height=8
        )
        self.coo_status_text.tag_configure(
            "coo_error", foreground="#B42318", font=("TkDefaultFont", 10, "bold")
        )
        self.coo_status_text.pack(fill="both", expand=True)
        panes.add(coo_frame, weight=2)

    def _build_approvals_tab(self):
        toolbar = ttk.Frame(self.approvals_frame)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(
            toolbar, text="Refresh Approvals", command=self.refresh_approvals
        ).pack(side="right")

        columns = ("task", "department", "gate", "status")
        self.approvals_tree = ttk.Treeview(
            self.approvals_frame,
            columns=columns,
            show="headings",
            height=8,
        )
        for col, heading, width in (
            ("task", "Task", 280),
            ("department", "Department", 140),
            ("gate", "Gate", 180),
            ("status", "Status", 120),
        ):
            self.approvals_tree.heading(col, text=heading)
            self.approvals_tree.column(col, width=width, anchor="w")
        self.approvals_tree.pack(fill="x")
        self.approvals_tree.bind("<<TreeviewSelect>>", self._on_approval_selected)

        detail_frame = ttk.LabelFrame(
            self.approvals_frame, text="Approval Details", padding=4
        )
        detail_frame.pack(fill="both", expand=True, pady=8)
        self.approval_detail = scrolledtext.ScrolledText(
            detail_frame, wrap=tk.WORD, height=12
        )
        self.approval_detail.pack(fill="both", expand=True)

        action_frame = ttk.Frame(self.approvals_frame)
        action_frame.pack(fill="x")
        ttk.Label(action_frame, text="Feedback:").pack(side="left")
        self.approval_feedback = ttk.Entry(action_frame)
        self.approval_feedback.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(action_frame, text="Approve", command=self.approve_selected).pack(
            side="right"
        )
        ttk.Button(
            action_frame, text="Request Changes", command=self.request_changes_selected
        ).pack(side="right", padx=(0, 8))
        ttk.Button(action_frame, text="Reject", command=self.reject_selected).pack(
            side="right", padx=(0, 8)
        )

    def _build_audit_tab(self):
        toolbar = ttk.Frame(self.audit_frame)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Refresh Audit", command=self.refresh_audit).pack(
            side="right"
        )

        self.audit_text = scrolledtext.ScrolledText(self.audit_frame, wrap=tk.WORD)
        self.audit_text.pack(fill="both", expand=True)

    def _build_guide_tab(self):
        self._add_tab_description(self.guide_frame, "Guide")
        canvas = tk.Canvas(self.guide_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            self.guide_frame, orient="vertical", command=canvas.yview
        )
        content = ttk.Frame(canvas)
        content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._add_field_guide(content, "Window Controls", DESKTOP_CHROME_GUIDE)
        self._add_field_guide(
            content, "Top-Level Tabs", tuple(DESKTOP_TAB_GUIDE.items())
        )
        self._add_field_guide(content, "Chat Fields", CHAT_FIELD_GUIDE)
        self._add_field_guide(
            content, "Chat Sub-Tabs", tuple(CHAT_TARGET_GUIDE.items())
        )
        self._add_field_guide(content, "Settings Fields", SETTINGS_FIELD_GUIDE)
        self._add_field_guide(content, "Dashboard Fields", DASHBOARD_FIELD_GUIDE)
        self._add_field_guide(content, "Approval Fields", APPROVALS_FIELD_GUIDE)
        self._add_field_guide(content, "Audit Fields", AUDIT_FIELD_GUIDE)

    def refresh_settings_fields(self):
        if not self.coder:
            messagebox.showinfo("Settings", "Aider backend is still starting.")
            return
        root = Path(self.coder.root)
        self.env_path = root / ".env"
        self.conf_path = root / ".aider.conf.yml"
        env_values = read_env_values(self.env_path)
        conf_text = (
            self.conf_path.read_text(encoding="utf-8")
            if self.conf_path.exists()
            else ""
        )
        conf_values = parse_conf_text(conf_text)
        current_model = self.coder.main_model
        defaults = {
            "model": current_model.name,
            "weak-model": current_model.weak_model.name,
            "editor-model": current_model.editor_model.name,
        }
        for key, var in self.model_vars.items():
            var.set(conf_values.get(key) or defaults.get(key, ""))
        for key, var in self.api_key_vars.items():
            var.set(env_values.get(key, ""))
        for agent_name, var in self.agent_model_vars.items():
            var.set(env_values.get(agent_model_env_name(agent_name), ""))
        for agent_name, var in self.agent_caching_vars.items():
            var.set(
                env_values.get(agent_caching_env_name(agent_name), "true").lower()
                != "false"
            )
        for agent_name, var in self.agent_api_key_vars.items():
            var.set(env_values.get(agent_api_key_env_name(agent_name), ""))
        for agent_name, var in self.agent_local_vars.items():
            var.set(env_values.get(agent_local_env_name(agent_name), ""))
        self.provider_keys_text.delete("1.0", tk.END)
        self.conf_text.delete("1.0", tk.END)
        self.conf_text.insert(tk.END, conf_text)

    def save_settings(self):
        if not self.coder:
            messagebox.showinfo("Settings", "Aider backend is still starting.")
            return
        root = Path(self.coder.root)
        self.env_path = root / ".env"
        self.conf_path = root / ".aider.conf.yml"
        provider_updates = collect_provider_key_updates(
            self.api_key_vars["OPENAI_API_KEY"].get(),
            self.api_key_vars["ANTHROPIC_API_KEY"].get(),
            self.api_key_vars["OPENROUTER_API_KEY"].get(),
            self.provider_keys_text.get("1.0", tk.END),
        )
        agent_models = {name: var.get() for name, var in self.agent_model_vars.items()}
        agent_caching = {
            name: var.get() for name, var in self.agent_caching_vars.items()
        }
        agent_api_keys = {
            name: var.get() for name, var in self.agent_api_key_vars.items()
        }
        agent_local_settings = {
            name: var.get() for name, var in self.agent_local_vars.items()
        }
        env_updates = provider_updates | collect_agent_env_updates(
            agent_models, agent_caching, agent_api_keys, agent_local_settings
        )
        write_env_updates(self.env_path, env_updates)
        apply_env_updates(env_updates)

        model_updates = {key: var.get().strip() for key, var in self.model_vars.items()}
        write_conf_text(
            self.conf_path, self.conf_text.get("1.0", tk.END), model_updates
        )
        if self.apply_model_now_var.get():
            self._apply_model_settings(model_updates)
        self._restart_company_session()
        self.refresh_settings_fields()
        self._append_chat(
            "System",
            f"Settings saved to {self.env_path} and {self.conf_path}. New Company agent sessions will use the updated configuration.",
            tag="system",
        )
        messagebox.showinfo(
            "Settings saved", "Aider and Company agent settings were saved."
        )

    def _apply_model_settings(self, model_updates: dict[str, str]):
        if not self.coder:
            return
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
        self.coder = next_coder

    def _restart_company_session(self):
        if self.company:
            self.company.shutdown()
        if self.coder:
            self.company = get_desktop_company_session(self.coder)

    def _init_backend(self):
        def init():
            try:
                coder = cli_main(argv=self.argv, return_coder=True)
                if not isinstance(coder, Coder):
                    raise ValueError(coder)
                if not coder.repo:
                    raise ValueError(
                        "The Tkinter desktop launcher must be run inside a git repo."
                    )
                company = get_desktop_company_session(coder)
                self._ui_queue.put(("backend_ready", (coder, company)))
            except Exception as err:
                self._ui_queue.put(("backend_error", err))

        threading.Thread(target=init, name="aider-tk-bootstrap", daemon=True).start()

    def send_chat_message(self):
        prompt = self.chat_entry.get().strip()
        if not prompt:
            return
        target = self._current_chat_target()
        self.chat_entry.delete(0, tk.END)
        self._append_chat("You", prompt, tag="user", target=target)

        if target == "Direct Aider" and self.coder:
            self.turns_this_session += 1
            future = _submit_threaded(lambda: self.coder.run(with_message=prompt))
            self._track_future(future, "Aider response", target=target)
            self._set_busy(True, "Aider is responding…")
        elif (
            target == "Company Workflow" and self.company and self.company.orchestrator
        ):
            self.turns_this_session += 1
            future = self.company.run_auto(prompt)
            self._track_future(future, "Company response", target=target)
            self._set_busy(True, "Company workflow running…")
        elif self.company:
            self.turns_this_session += 1
            agent_name = target.lower()
            future = self.company.chat_with_agent(agent_name, prompt)
            self._track_future(future, f"{target} agent response", target=target)
            self._set_busy(True, f"{target} agent is responding…")
        else:
            self._append_chat(
                "Aider",
                "Backend is still starting. Please try again shortly.",
                tag="system",
                target=target,
            )

    def _current_chat_target(self) -> str:
        if not hasattr(self, "chat_notebook"):
            return "Company Workflow"
        selected = self.chat_notebook.select()
        return self.chat_notebook.tab(selected, "text") or "Company Workflow"

    def _append_chat(
        self, sender: str, message: str, tag: str = "aider", target: str | None = None
    ):
        widget = self.chat_transcripts.get(
            target or self._current_chat_target(), self.chat_text
        )
        widget.config(state="normal")
        label_tag = tag if tag in {"user", "aider", "system", "error"} else "aider"
        widget.insert(tk.END, f"{sender}: ", label_tag)
        _insert_text_with_code_tags(widget, str(message).rstrip() + "\n\n")
        widget.see(tk.END)
        widget.config(state="disabled")

    def refresh_dashboard(self):
        if not self.company:
            self._write_text(self.dashboard_text, "Company backend is not ready yet.")
            self._write_text(self.coo_status_text, "Company backend is not ready yet.")
            return
        metrics = self.company.dashboard_metrics(
            turns_this_session=self.turns_this_session
        )
        for key, label in self.metric_labels.items():
            label.config(text=str(metrics.get(key, "—")))
        status = self.company.company_status()
        self._write_text(self.dashboard_text, status)
        self._write_text(self.deliverables_text, self._format_deliverables())
        coo_status = self._format_coo_status()
        self._write_text(self.coo_status_text, coo_status)
        if "Recent COO errors:" in coo_status:
            start = self.coo_status_text.search("Recent COO errors:", "1.0", tk.END)
            if start:
                self.coo_status_text.tag_add("coo_error", start, tk.END)

    def _format_coo_status(self) -> str:
        if not self.company:
            return "Company backend is not ready yet."
        try:
            status = self.company.coo_status()
        except Exception as err:
            return f"COO status unavailable: {err}"
        if not status:
            return "No CEO/COO status is available yet."
        current_route = status.get("current_route") or {}
        last_action = status.get("last_coo_action") or {}
        lines = [
            f"Session: {status.get('session_id')}",
            f"Status: {status.get('status')}",
            f"COO action: {last_action.get('action', '—')}",
            f"Active department: {status.get('active_department') or '—'}",
            (
                "Current route: "
                f"{current_route.get('strategy', '—')} → "
                f"{current_route.get('target', '—')}"
            ),
            f"Prompt caching: {self.company.caching_status()}",
        ]
        coo_memory = status.get("coo_memory") or []
        if coo_memory:
            lines.append(f"COO memory notes: {len(coo_memory)}")
        error_count = int(status.get("error_count", 0) or 0)
        if error_count > 0:
            last_error = status.get("last_error") or {}
            lines.extend(
                [
                    "",
                    "Recent COO errors:",
                    f"- Count: {error_count}",
                    (
                        "- Last: "
                        f"{last_error.get('error_type', 'unknown_error')} "
                        f"after {last_error.get('retries', 0)} retries — "
                        f"{last_error.get('message', '')}"
                    ),
                    f"- Recovery: {last_error.get('recovery_suggestion', 'review the COO event')}",
                ]
            )
            if last_error.get("escalate_to_human"):
                lines.extend(
                    [
                        "- Human escalation: pending",
                        f"- Approval task: {last_error.get('approval_task_id', 'open Approvals tab')}",
                    ]
                )
            recent_errors = status.get("recent_errors") or []
            if len(recent_errors) > 1:
                lines.extend(["", "Recent error history:"])
                for error in recent_errors[-3:]:
                    lines.append(
                        "- "
                        f"{error.get('error_type', 'unknown_error')}: "
                        f"{error.get('recovery_suggestion', 'review')}"
                    )
            pending_escalations = status.get("pending_human_escalations") or []
            if pending_escalations:
                lines.extend(
                    [
                        "",
                        "Pending human escalations:",
                        *(f"- {task_id}" for task_id in pending_escalations[-5:]),
                    ]
                )
        lines.extend(["", "Recent COO events:"])
        events = status.get("recent_events") or []
        if events:
            lines.extend(f"- {event}" for event in events[-20:])
        else:
            lines.append("- No bus events yet.")
        summary = status.get("last_deliverable_summary")
        if summary:
            lines.extend(["", "Last deliverable summary:", str(summary)[:1200]])
        return "\n".join(lines)

    def refresh_approvals(self):
        for item in self.approvals_tree.get_children():
            self.approvals_tree.delete(item)
        self.selected_approval_id = None
        self._write_text(self.approval_detail, "Select an approval to view details.")
        if not self.company:
            return
        approvals = self.company.pending_approvals()
        if not approvals:
            self._write_text(self.approval_detail, "No pending approvals.")
            return
        for approval in approvals:
            task_id = str(approval.get("task_id") or approval.get("id") or "")
            self.approvals_tree.insert(
                "",
                tk.END,
                iid=task_id,
                values=(
                    task_id,
                    approval.get("department", "unknown"),
                    approval.get("gate_name", "approval"),
                    approval.get("status", "pending"),
                ),
            )

    def refresh_audit(self):
        if not self.company:
            self._write_text(self.audit_text, "Company backend is not ready yet.")
            return
        self._write_text(self.audit_text, self.company.audit_log(limit=30))

    def refresh_all(self):
        self.refresh_dashboard()
        self.refresh_approvals()
        self.refresh_audit()

    def approve_selected(self):
        self._handle_approval_action("approve")

    def reject_selected(self):
        self._handle_approval_action("reject")

    def request_changes_selected(self):
        self._handle_approval_action("request_changes")

    def _handle_approval_action(self, action: str):
        if not self.company or not self.selected_approval_id:
            messagebox.showinfo("Approvals", "Select a pending approval first.")
            return
        feedback = self.approval_feedback.get().strip()
        if action == "approve":
            future = self.company.approve(self.selected_approval_id, feedback)
            label = f"Approve {self.selected_approval_id}"
        elif action == "reject":
            future = self.company.reject(
                self.selected_approval_id, feedback or "Rejected from Tkinter desktop"
            )
            label = f"Reject {self.selected_approval_id}"
        else:
            if not feedback:
                messagebox.showinfo(
                    "Approvals", "Add feedback before requesting changes."
                )
                return
            future = self.company.request_changes(self.selected_approval_id, feedback)
            label = f"Request changes {self.selected_approval_id}"
        self._track_future(future, label)
        self._set_busy(True, f"{label} submitted…")

    def _on_approval_selected(self, _event=None):
        selection = self.approvals_tree.selection()
        if not selection:
            return
        task_id = selection[0]
        self.selected_approval_id = task_id
        approval = self._find_approval(task_id)
        self._write_text(
            self.approval_detail,
            _format_approval_detail(approval) if approval else task_id,
        )

    def _find_approval(self, task_id: str) -> dict[str, Any] | None:
        if not self.company:
            return None
        for approval in self.company.pending_approvals():
            if str(approval.get("task_id") or approval.get("id") or "") == task_id:
                return approval
        return None

    def _format_deliverables(self) -> str:
        if not self.company:
            return "No Company session."
        deliverables = self.company.recent_deliverables()
        if not deliverables:
            return "No deliverables yet."
        sections = []
        for item in deliverables:
            files = item.get("files") or []
            files_text = ", ".join(map(str, files)) if files else "No files listed"
            sections.append(
                f"{item.get('label', 'Deliverable')} "
                f"[{item.get('department', 'company')} / {item.get('status', 'unknown')}]\n"
                f"{str(item.get('summary', '')).strip()}\nFiles: {files_text}"
            )
        return "\n\n".join(sections)

    def _track_future(
        self, future: concurrent.futures.Future, label: str, target: str | None = None
    ):
        self._future_labels[future] = (label, target)
        future.add_done_callback(lambda done: self._ui_queue.put(("future_done", done)))

    def _poll_background(self):
        if self._closing:
            return
        self._drain_ui_queue()
        self._drain_company_events()
        self.root.after(POLL_INTERVAL_MS, self._poll_background)

    def _drain_ui_queue(self):
        while True:
            try:
                kind, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "backend_ready":
                self.coder, self.company = payload
                repo = Path(self.coder.root).name
                self.repo_label.config(text=f"Repo: {repo}")
                self._append_chat(
                    "System", "Aider Company Mode is ready.", tag="system"
                )
                self._set_busy(False, "Ready")
                self.refresh_settings_fields()
                self.refresh_all()
            elif kind == "backend_error":
                self._set_busy(False, "Backend failed")
                self._append_chat("Error", str(payload), tag="error")
                messagebox.showerror("Aider Plus startup failed", str(payload))
            elif kind == "future_done":
                self._handle_future_done(payload)

    def _drain_company_events(self):
        if not self.company:
            return
        events, _version = self.company.drain_events()
        for event in events:
            message = (
                getattr(event, "message", None)
                or getattr(event, "payload", None)
                or str(event)
            )
            department = (
                getattr(event, "department", None)
                or getattr(event, "origin", None)
                or "Company"
            )
            self._append_chat(company_label(department), str(message), tag="aider")
        if events:
            self.refresh_all()
        if self.company.background_error:
            self._append_chat("Error", self.company.background_error, tag="error")
            self.company.background_error = None

    def _handle_future_done(self, future: concurrent.futures.Future):
        label_info = self._future_labels.pop(future, ("Background task", None))
        if isinstance(label_info, tuple):
            label, target = label_info
        else:
            label, target = label_info, None
        try:
            result = future.result()
        except concurrent.futures.CancelledError:
            self._append_chat(
                "System", f"{label} was cancelled.", tag="system", target=target
            )
        except Exception as err:
            self._append_chat(
                "Error", f"{label} failed: {err}", tag="error", target=target
            )
            self._set_busy(False, f"{label} failed")
        else:
            if result:
                self._append_chat(
                    "Aider", _format_result(result), tag="aider", target=target
                )
            self._set_busy(False, "Ready")
            self.refresh_all()

    def _set_busy(self, busy: bool, status: str):
        self.status_label.config(text=status)
        state = tk.DISABLED if busy else tk.NORMAL
        if hasattr(self, "send_button"):
            self.send_button.config(state=state)
        if hasattr(self, "chat_entry"):
            self.chat_entry.config(state=state)

    @staticmethod
    def _write_text(widget: scrolledtext.ScrolledText, text: str):
        widget.config(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text or "")
        widget.see("1.0")
        widget.config(state="normal")

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def on_close(self):
        self._closing = True
        if self.company:
            self.company.shutdown()
        self.root.destroy()


def _insert_text_with_code_tags(widget: scrolledtext.ScrolledText, text: str):
    in_code = False
    for line in text.splitlines(keepends=True):
        if line.strip().startswith("```"):
            in_code = not in_code
            widget.insert(tk.END, line, "code")
        else:
            widget.insert(tk.END, line, "code" if in_code else None)


def _format_approval_detail(approval: dict[str, Any]) -> str:
    return json.dumps(approval, indent=2, default=str)


def _format_result(result: Any) -> str:
    if isinstance(result, dict):
        summary = result.get("summary") or result.get("content") or result.get("status")
        if summary:
            details = []
            for key in ("files", "files_changed", "commits", "status"):
                value = result.get(key)
                if value:
                    details.append(f"{company_label(key)}: {value}")
            return str(summary) + ("\n\n" + "\n".join(details) if details else "")
        return json.dumps(result, indent=2, default=str)
    return str(result)


def _strip_desktop_args(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None
    return [
        arg
        for arg in argv
        if arg not in {"--desktop", "--no-desktop", "--desktop-tk", "--no-desktop-tk"}
    ]


def _submit_threaded(fn):
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="aider-tk"
    )
    future = executor.submit(fn)
    future.add_done_callback(lambda _done: executor.shutdown(wait=False))
    return future


def launch_desktop_gui(
    args=None, write_streamlit_credentials=None, debug: bool = False
):
    """Launch the zero-dependency native Tkinter desktop."""
    app = AiderPlusDesktop(argv=args)
    app.run()


def main(argv: list[str] | None = None):
    launch_desktop_gui(args=argv)


if __name__ == "__main__":
    main()
