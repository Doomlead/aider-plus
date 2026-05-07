#!/usr/bin/env python

import asyncio
import os
import random
import re
import sys
import threading
import uuid
from pathlib import Path

import streamlit as st

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
        self.pending_runs = []
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

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

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
        self.product = ProductDepartment(project_memory=project_memory)
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
            self.submit_background(department.run_loop())
        self.orchestrator.on_deliverable(self._record_company_message)
        self.submit_background(self.orchestrator.recover_pending_approvals())

    async def _record_company_message(self, message):
        self.events.append(message)

    def submit_background(self, coro, label=None):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        if label:
            self.pending_runs.append((label, future))
        return future

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

    def approve(self, task_id: str):
        return self.submit_background(
            self.orchestrator.handle_approval_response(
                task_id,
                True,
                source="desktop",
                metadata={"approved_by": "desktop"},
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
        return AuditLogViewer.from_project_memory(self.coder.project_memory).render_text(
            limit=limit
        )

    def company_status(self) -> str:
        return self.orchestrator.company_status()

    def persist(self):
        conversation_memory = getattr(self.coder, "conversation_memory", None)
        project_memory = getattr(self.coder, "project_memory", None)
        if isinstance(conversation_memory, ConversationMemory) and isinstance(
            project_memory, ProjectMemory
        ):
            consolidate_conversation(conversation_memory, project_memory)
            project_memory.persist()


@st.cache_resource
def get_desktop_company_session(_coder):
    return DesktopCompanySession(_coder)


def format_desktop_approval(approval: dict) -> str:
    gate_name = approval.get("gate_name", "prd_approval")
    gate_label = (
        gate_name.replace("prd", "PRD")
        .replace("_", " ")
        .title()
        .replace("Prd", "PRD")
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
                if self.button(f"Undo commit `{commit_hash}`", key=f"undo_{commit_hash}"):
                    self.do_undo(commit_hash)

    def do_sidebar(self):
        with st.sidebar:
            st.title("Aider")
            if st.button("⚙️ Settings", key="open_settings"):
                self.state.show_settings = not self.state.show_settings
            # self.cmds_tab, self.settings_tab = st.tabs(["Commands", "Settings"])

            if self.state.show_settings:
                self.do_settings_tab()
                st.divider()

            self.do_company_tab()
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
        st.subheader("Company")
        st.caption(
            "Desktop controls for the same Product → UX → Engineering → QA → DevOps "
            "workflow exposed in Discord."
        )

        self.state.company_enabled = st.toggle(
            "Use Company workflow for chat",
            value=self.state.company_enabled,
            disabled=self.prompt_pending(),
            help=(
                "Routes prompts through Product prototyping first, then Engineering "
                "for follow-up work. Turn this off to use the classic direct Aider chat."
            ),
        )
        self.state.company_route = st.selectbox(
            "Company route",
            ["Auto", "Prototype", "Engineering"],
            index=["Auto", "Prototype", "Engineering"].index(self.state.company_route),
            disabled=self.prompt_pending() or not self.state.company_enabled,
            help=(
                "Auto mirrors Discord's human entry point. Prototype starts with Product/PRD. "
                "Engineering skips PRD generation for existing-project tasks."
            ),
        )

        if not self.state.company_enabled:
            return

        company = self.get_company()
        if st.button("🔄 Refresh company state", key="company_refresh"):
            st.rerun()

        self.do_company_pending_runs(company)
        self.do_company_approvals(company)

        with st.expander("🏢 Dashboard", expanded=False):
            st.code(company.company_status())

        with st.expander("🧾 Audit log", expanded=False):
            limit = st.slider("Audit events", 1, 25, 10, key="audit_log_limit")
            st.code(company.audit_log(limit=limit))

        with st.expander("📡 Company events", expanded=False):
            if not company.events:
                st.caption("No company events yet.")
            for event in company.events[-10:]:
                self.render_company_event(event)

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
                    summary = result.get("summary") if isinstance(result, dict) else result
                    if summary:
                        msg = f"{label} completed.\n\n{summary}"
                        self.state.messages.append({"role": "assistant", "content": msg})
                        st.success(f"{label} completed.")
            else:
                remaining.append((label, future))
                st.info(f"{label} is running in the background.")
        company.pending_runs = remaining

    def do_company_approvals(self, company):
        pending = [
            approval
            for approval in company.pending_approvals()
            if approval.get("status") == "pending"
        ]
        if not pending:
            st.caption("No pending approvals.")
            return

        st.warning(f"{len(pending)} approval request(s) need a decision.")
        for approval in pending:
            task_id = str(approval.get("task_id"))
            with st.expander(f"Approval: {task_id}", expanded=True):
                st.markdown(format_desktop_approval(approval))
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ Approve", key=f"approve_{task_id}"):
                        company.approve(task_id)
                        st.rerun()
                with col2:
                    if st.button("❌ Reject", key=f"reject_{task_id}"):
                        company.reject(task_id)
                        st.rerun()
                feedback = st.text_area(
                    "Request changes feedback",
                    key=f"feedback_{task_id}",
                    placeholder="Describe what should change before this is approved.",
                )
                if st.button("📝 Request changes", key=f"changes_{task_id}"):
                    company.request_changes(
                        task_id, feedback or "Changes requested from desktop"
                    )
                    st.rerun()

    def render_company_event(self, event):
        if isinstance(event, EventMessage):
            if event.event == CompanyEvent.APPROVAL_REQUIRED:
                st.warning(f"Approval required: {event.task_id}")
                st.json(event.payload)
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

    def do_settings_tab(self):
        st.subheader("Settings")
        st.caption("Configure API keys, model defaults, and provider settings.")

        env_values = self._read_env_values(self.env_path)
        conf_values = self._read_conf_values(self.conf_path)

        with st.form("settings_form", clear_on_submit=False):
            model = st.text_input("Main model", value=conf_values.get("model", ""))
            weak_model = st.text_input("Weak model", value=conf_values.get("weak-model", ""))
            editor_model = st.text_input("Editor model", value=conf_values.get("editor-model", ""))
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
                {"model": model.strip(), "weak-model": weak_model.strip(), "editor-model": editor_model.strip()},
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
                st.write("It's best to keep aider's internal files out of your git repo.")
                self.button("Add `.aider*` to `.gitignore`", key=random.random(), help="?")

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
            self.info("Cleared chat history. Now the LLM can't see anything before this line.")

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

        self.do_messages_container()
        self.do_sidebar()

        user_inp = st.chat_input("Say something")
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

        if self.state.company_enabled:
            self.process_company_chat(prompt)
            return

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
            future = self.get_company().start_prototype(prompt)
        elif route == "Engineering":
            future = self.get_company().run_instruction(prompt)
        else:
            future = self.get_company().run_auto(prompt)

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
            self.scraper = Scraper(print_error=self.info, playwright_available=has_playwright())

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
