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

from aider.agent import AiderAgentLoop
from aider.agent.loop import AgentLoopConfig
from aider.coders import Coder
from aider.company.audit import AuditLogViewer
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


logger = logging.getLogger(__name__)
_COMPANY_SESSIONS: dict[str, "DesktopCompanySession"] = {}
_COMPANY_SESSIONS_LOCK = threading.Lock()


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
        self.product = None
        self.ux = None
        self.engineering = None
        self.qa = None
        self.devops = None
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
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
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
        agent_loop = AiderAgentLoop(
            coder=self.coder,
            config=AgentLoopConfig(use_architect_mode=True),
        )
        project_memory = self.coder.project_memory
        conversation_memory = self.coder.conversation_memory
        self.engineering = EngineeringDepartment(
            project_memory=project_memory,
            agent_loop=agent_loop,
            conversation_memory=conversation_memory,
        )
        self.product = ProductDepartment(
            project_memory=project_memory,
            agent_loop=agent_loop,
            conversation_memory=conversation_memory,
        )
        self.ux = UXDepartment(
            project_memory=project_memory,
            agent_loop=agent_loop,
            conversation_memory=conversation_memory,
        )
        self.qa = QADepartment(project_memory=project_memory)
        self.devops = DevOpsDepartment(project_memory=project_memory)
        self.orchestrator = CompanyOrchestrator(project_memory=project_memory)
        self.orchestrator.active_project = self.active_project
        for department in (
            self.product,
            self.ux,
            self.engineering,
            self.qa,
            self.devops,
        ):
            self.orchestrator.register(department)
        for department in (
            self.product,
            self.ux,
            self.engineering,
            self.qa,
            self.devops,
        ):
            self.submit_background(department.run_loop(), f"{department.name} run loop", service=True)
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
        if self.loop_thread.is_alive() and threading.current_thread() is not self.loop_thread:
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
        deliverable = await self.product.process(task)
        await self.orchestrator._route(deliverable)
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
        deliverable = await self.engineering.process(task)
        await self.orchestrator._route(deliverable)
        result = {
            "summary": deliverable.payload,
            "content": deliverable.payload,
            "files": deliverable.metadata.get("files", []),
            "files_changed": deliverable.metadata.get("files", []),
            "commits": deliverable.metadata.get("commits", []),
            "diffs": deliverable.metadata.get("diffs", []),
            "status": deliverable.status,
        }
        self.coder.project_memory.update({"last_prompt": prompt, "last_result": deliverable.payload})
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

    def pending_approvals(self):
        return self.orchestrator.state.get_pending_approvals()

    def audit_log(self, limit: int = 10) -> str:
        return AuditLogViewer.from_project_memory(self.coder.project_memory).render_text(limit=limit)

    def company_status(self) -> str:
        return self.orchestrator.company_status()

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
        pending = [approval for approval in self.pending_approvals() if approval.get("status") == "pending"]
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
        self.repo_label = ttk.Label(header, text="Initializing…")
        self.repo_label.pack(side="right")

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)

        self.chat_frame = ttk.Frame(self.notebook, padding=8)
        self.dashboard_frame = ttk.Frame(self.notebook, padding=8)
        self.approvals_frame = ttk.Frame(self.notebook, padding=8)
        self.audit_frame = ttk.Frame(self.notebook, padding=8)

        self.notebook.add(self.chat_frame, text="Chat")
        self.notebook.add(self.dashboard_frame, text="Company Dashboard")
        self.notebook.add(self.approvals_frame, text="Approvals")
        self.notebook.add(self.audit_frame, text="Audit")

        self._build_chat_tab()
        self._build_dashboard_tab()
        self._build_approvals_tab()
        self._build_audit_tab()

        self.status_label = ttk.Label(outer, text="Ready", anchor="w", style="Status.TLabel")
        self.status_label.pack(fill="x", pady=(8, 0))

    def _build_chat_tab(self):
        self.chat_text = scrolledtext.ScrolledText(
            self.chat_frame,
            wrap=tk.WORD,
            state="disabled",
            font=("TkDefaultFont", 10),
            padx=10,
            pady=10,
        )
        self.chat_text.pack(fill="both", expand=True)
        self.chat_text.tag_configure("user", foreground="#155EEF", font=("TkDefaultFont", 10, "bold"))
        self.chat_text.tag_configure("aider", foreground="#047857", font=("TkDefaultFont", 10, "bold"))
        self.chat_text.tag_configure("system", foreground="#6B7280", font=("TkDefaultFont", 10, "italic"))
        self.chat_text.tag_configure("error", foreground="#B42318", font=("TkDefaultFont", 10, "bold"))
        self.chat_text.tag_configure("code", font=("TkFixedFont", 10), background="#F3F4F6")

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

        self._append_chat(
            "System",
            "Company Mode is starting. Send a request once the status bar says Ready.",
            tag="system",
        )

    def _build_dashboard_tab(self):
        toolbar = ttk.Frame(self.dashboard_frame)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Refresh Dashboard", command=self.refresh_dashboard).pack(side="right")

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
        self.dashboard_text = scrolledtext.ScrolledText(status_frame, wrap=tk.WORD, height=12)
        self.dashboard_text.pack(fill="both", expand=True)
        panes.add(status_frame, weight=3)

        deliverables_frame = ttk.LabelFrame(panes, text="Recent Deliverables", padding=4)
        self.deliverables_text = scrolledtext.ScrolledText(deliverables_frame, wrap=tk.WORD, height=8)
        self.deliverables_text.pack(fill="both", expand=True)
        panes.add(deliverables_frame, weight=2)

    def _build_approvals_tab(self):
        toolbar = ttk.Frame(self.approvals_frame)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Refresh Approvals", command=self.refresh_approvals).pack(side="right")

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

        detail_frame = ttk.LabelFrame(self.approvals_frame, text="Approval Details", padding=4)
        detail_frame.pack(fill="both", expand=True, pady=8)
        self.approval_detail = scrolledtext.ScrolledText(detail_frame, wrap=tk.WORD, height=12)
        self.approval_detail.pack(fill="both", expand=True)

        action_frame = ttk.Frame(self.approvals_frame)
        action_frame.pack(fill="x")
        ttk.Label(action_frame, text="Feedback:").pack(side="left")
        self.approval_feedback = ttk.Entry(action_frame)
        self.approval_feedback.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(action_frame, text="Approve", command=self.approve_selected).pack(side="right")
        ttk.Button(action_frame, text="Request Changes", command=self.request_changes_selected).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(action_frame, text="Reject", command=self.reject_selected).pack(side="right", padx=(0, 8))

    def _build_audit_tab(self):
        toolbar = ttk.Frame(self.audit_frame)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Refresh Audit", command=self.refresh_audit).pack(side="right")

        self.audit_text = scrolledtext.ScrolledText(self.audit_frame, wrap=tk.WORD)
        self.audit_text.pack(fill="both", expand=True)

    def _init_backend(self):
        def init():
            try:
                coder = cli_main(argv=self.argv, return_coder=True)
                if not isinstance(coder, Coder):
                    raise ValueError(coder)
                if not coder.repo:
                    raise ValueError("The Tkinter desktop launcher must be run inside a git repo.")
                company = get_desktop_company_session(coder)
                self._ui_queue.put(("backend_ready", (coder, company)))
            except Exception as err:
                self._ui_queue.put(("backend_error", err))

        threading.Thread(target=init, name="aider-tk-bootstrap", daemon=True).start()

    def send_chat_message(self):
        prompt = self.chat_entry.get().strip()
        if not prompt:
            return
        self.chat_entry.delete(0, tk.END)
        self._append_chat("You", prompt, tag="user")

        if self.company and self.company.orchestrator:
            self.turns_this_session += 1
            future = self.company.run_auto(prompt)
            self._track_future(future, "Company response")
            self._set_busy(True, "Company workflow running…")
        elif self.coder:
            self.turns_this_session += 1
            future = _submit_threaded(lambda: self.coder.run(with_message=prompt))
            self._track_future(future, "Aider response")
            self._set_busy(True, "Aider is responding…")
        else:
            self._append_chat("Aider", "Backend is still starting. Please try again shortly.", tag="system")

    def _append_chat(self, sender: str, message: str, tag: str = "aider"):
        self.chat_text.config(state="normal")
        label_tag = tag if tag in {"user", "aider", "system", "error"} else "aider"
        self.chat_text.insert(tk.END, f"{sender}: ", label_tag)
        _insert_text_with_code_tags(self.chat_text, str(message).rstrip() + "\n\n")
        self.chat_text.see(tk.END)
        self.chat_text.config(state="disabled")

    def refresh_dashboard(self):
        if not self.company:
            self._write_text(self.dashboard_text, "Company backend is not ready yet.")
            return
        metrics = self.company.dashboard_metrics(turns_this_session=self.turns_this_session)
        for key, label in self.metric_labels.items():
            label.config(text=str(metrics.get(key, "—")))
        status = self.company.company_status()
        self._write_text(self.dashboard_text, status)
        self._write_text(self.deliverables_text, self._format_deliverables())

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
            future = self.company.reject(self.selected_approval_id, feedback or "Rejected from Tkinter desktop")
            label = f"Reject {self.selected_approval_id}"
        else:
            if not feedback:
                messagebox.showinfo("Approvals", "Add feedback before requesting changes.")
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
        self._write_text(self.approval_detail, _format_approval_detail(approval) if approval else task_id)

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

    def _track_future(self, future: concurrent.futures.Future, label: str):
        self._future_labels[future] = label
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
                self._append_chat("System", "Aider Company Mode is ready.", tag="system")
                self._set_busy(False, "Ready")
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
            message = getattr(event, "message", None) or getattr(event, "payload", None) or str(event)
            department = getattr(event, "department", None) or getattr(event, "origin", None) or "Company"
            self._append_chat(str(department).title(), str(message), tag="aider")
        if events:
            self.refresh_all()
        if self.company.background_error:
            self._append_chat("Error", self.company.background_error, tag="error")
            self.company.background_error = None

    def _handle_future_done(self, future: concurrent.futures.Future):
        label = self._future_labels.pop(future, "Background task")
        try:
            result = future.result()
        except concurrent.futures.CancelledError:
            self._append_chat("System", f"{label} was cancelled.", tag="system")
        except Exception as err:
            self._append_chat("Error", f"{label} failed: {err}", tag="error")
            self._set_busy(False, f"{label} failed")
        else:
            if result:
                self._append_chat("Aider", _format_result(result), tag="aider")
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
                    details.append(f"{key.replace('_', ' ').title()}: {value}")
            return str(summary) + ("\n\n" + "\n".join(details) if details else "")
        return json.dumps(result, indent=2, default=str)
    return str(result)


def _strip_desktop_args(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None
    return [arg for arg in argv if arg not in {"--desktop", "--no-desktop", "--desktop-tk", "--no-desktop-tk"}]


def _submit_threaded(fn):
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="aider-tk")
    future = executor.submit(fn)
    future.add_done_callback(lambda _done: executor.shutdown(wait=False))
    return future


def launch_desktop_gui(args=None, write_streamlit_credentials=None, debug: bool = False):
    """Launch the zero-dependency native Tkinter desktop."""
    app = AiderPlusDesktop(argv=args)
    app.run()


def main(argv: list[str] | None = None):
    launch_desktop_gui(args=argv)


if __name__ == "__main__":
    main()
