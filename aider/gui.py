#!/usr/bin/env python

import asyncio
import atexit
import concurrent.futures
import logging
import os
import random
import re
import sys
import threading
import uuid
from collections import deque
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from aider import urls
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
from aider.company.schemas import CompanyEvent, CompanyTask, EventMessage
from aider.dump import dump  # noqa: F401
from aider.io import InputOutput
from aider.main import main as cli_main
from aider.memory import ConversationMemory, ProjectMemory, consolidate_conversation
from aider.scrape import Scraper, has_playwright

logger = logging.getLogger(__name__)
_COMPANY_SESSIONS = {}
_COMPANY_SESSIONS_LOCK = threading.Lock()


COMPANY_PHASES = [
    "prototyping",
    "design",
    "development",
    "qa",
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
        self.ux = UXDepartment(project_memory=project_memory)
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
        deliverable = await self.product.process(task)
        await self.orchestrator._route(deliverable)
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

    def pending_approvals(self):
        return self.orchestrator.state.get_pending_approvals()

    def audit_log(self, limit: int = 10) -> str:
        return AuditLogViewer.from_project_memory(
            self.coder.project_memory
        ).render_text(limit=limit)

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
        if isinstance(event, EventMessage):
            if event.event == CompanyEvent.APPROVAL_REQUIRED:
                st.warning(f"Approval required: {event.task_id}")
                st.json(event.payload)
            elif event.event == CompanyEvent.LIFECYCLE:
                payload = event.payload or {}
                label = humanize_company_label(payload.get("name") or event.event)
                iteration = payload.get("iteration")
                suffix = f" (iteration {iteration})" if iteration is not None else ""
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
                "Company Dashboard",
                "Approvals",
                "Audit Log",
                "Project Memory",
            ]
        )
        with tabs[0]:
            self.do_chat_tab()
        with tabs[1]:
            self.do_company_dashboard_page()
        with tabs[2]:
            self.do_company_approvals_page()
        with tabs[3]:
            self.do_company_audit_log_page()
        with tabs[4]:
            self.do_project_memory_page()

    def do_chat_tab(self):
        if self.state.company_enabled:
            if self.state.company_bypass_next:
                st.info(
                    "Company Mode is active, but the next prompt will bypass it and use direct Aider chat."
                )
            else:
                st.info(
                    "Company Mode is active. Prompts are queued into the structured workflow; "
                    "use the dedicated tabs for dashboard, approvals, audit log, and memory."
                )
        else:
            st.info("Company Mode is paused. You are chatting directly with Aider.")
        self.do_messages_container()

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
        m1, m2, m3 = st.columns(3)
        m1.metric("Turns this session", metrics["turns_this_session"])
        m2.metric("Approvals pending", metrics["approvals_pending"])
        m3.metric("Last activity", metrics["last_activity"])

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
                st.write("**Preview**")
                st.write(
                    truncate_preview(deliverable.get("summary"), 1800)
                    or "No preview available."
                )

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

    def do_settings_tab(self):
        st.subheader("Settings")
        st.caption("Configure API keys, model defaults, and provider settings.")

        env_values = self._read_env_values(self.env_path)
        conf_values = self._read_conf_values(self.conf_path)

        with st.form("settings_form", clear_on_submit=False):
            model = st.text_input("Main model", value=conf_values.get("model", ""))
            weak_model = st.text_input(
                "Weak model", value=conf_values.get("weak-model", "")
            )
            editor_model = st.text_input(
                "Editor model", value=conf_values.get("editor-model", "")
            )
            openai_key = st.text_input(
                "OpenAI API key",
                value=env_values.get("OPENAI_API_KEY", ""),
                type="password",
            )
            anthropic_key = st.text_input(
                "Anthropic API key",
                value=env_values.get("ANTHROPIC_API_KEY", ""),
                type="password",
            )
            openrouter_key = st.text_input(
                "OpenRouter API key",
                value=env_values.get("OPENROUTER_API_KEY", ""),
                type="password",
            )
            provider_keys = st.text_area(
                "Other provider keys (one per line, eg GEMINI_API_KEY=...)", value=""
            )
            submitted = st.form_submit_button("Save settings")

        if submitted:
            updates = {}
            if openai_key:
                updates["OPENAI_API_KEY"] = openai_key.strip()
            if anthropic_key:
                updates["ANTHROPIC_API_KEY"] = anthropic_key.strip()
            if openrouter_key:
                updates["OPENROUTER_API_KEY"] = openrouter_key.strip()
            for line in provider_keys.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k and v:
                    updates[k.strip()] = v.strip()

            self._write_env_updates(self.env_path, updates)
            self._write_conf_updates(
                self.conf_path,
                {
                    "model": model.strip(),
                    "weak-model": weak_model.strip(),
                    "editor-model": editor_model.strip(),
                },
            )
            self.info(f"Saved settings to `{self.env_path}` and `{self.conf_path}`.")

    def _read_env_values(self, path: Path):
        vals = {}
        if not path.exists():
            return vals
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            vals[key.strip()] = val.strip().strip('"').strip("'")
        return vals

    def _write_env_updates(self, path: Path, updates: dict):
        if not updates:
            return
        existing = {}
        if path.exists():
            existing = self._read_env_values(path)
        existing.update(updates)
        lines = [f"{k}={v}" for k, v in sorted(existing.items())]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _read_conf_values(self, path: Path):
        vals = {}
        if not path.exists():
            return vals
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([a-zA-Z0-9_-]+)\s*:\s*(.+?)\s*$", line)
            if m:
                vals[m.group(1)] = m.group(2).strip().strip('"').strip("'")
        return vals

    def _write_conf_updates(self, path: Path, updates: dict):
        existing = {}
        if path.exists():
            existing = self._read_conf_values(path)
        for key, value in updates.items():
            if value:
                existing[key] = value
        lines = [f"{k}: {v}" for k, v in sorted(existing.items())]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

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
        self.coder = get_coder()
        self.state = get_state()

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

        chat_placeholder = (
            "Say something (direct Aider chat)"
            if not self.state.company_enabled or self.state.company_bypass_next
            else "Say something for the Company workflow"
        )
        user_inp = st.chat_input(chat_placeholder)
        if user_inp:
            self.prompt = user_inp

        if self.prompt_pending():
            self.process_chat()

        if not self.prompt:
            return

        self.state.prompt = self.prompt

        if self.prompt_as == "user":
            self.coder.io.add_to_input_history(self.prompt)

        self.state.input_history.append(self.prompt)

        if self.prompt_as:
            self.state.messages.append({"role": self.prompt_as, "content": self.prompt})
        if self.prompt_as == "user":
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
        self.state.prompt = None

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

    def process_company_chat(self, prompt):
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
        self.state.messages.append(
            {
                "role": "assistant",
                "content": (
                    f"Queued `{route}` company workflow for background processing. "
                    "Check the Company sidebar for progress and approvals."
                ),
            }
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
