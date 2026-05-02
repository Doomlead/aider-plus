from __future__ import annotations

import asyncio
import json
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aider.llm import litellm
from aider import models
from aider.agent.memory import AgentMemory
from aider.agent.tools import Tool, ToolRegistry


@dataclass
class AgentLoopConfig:
    max_iterations: int = 12
    max_repo_files: int = 50
    use_architect_mode: bool = True
    architect_model: str | None = None
    editor_model: str | None = None


@dataclass
class AgentContext:
    """Structured context bundle consumed by the LLM call."""

    system_prompt: str
    user_message: str
    recent_conversation: list[dict]
    recent_coder_results: list[dict]
    repository: dict[str, Any]
    project_instructions: str
    running_context: str = ""


    def get_user_turn_content(self) -> str:
        """Return a single user-turn payload for the current loop step."""
        parts: list[str] = []

        if self.running_context:
            parts.append(self.running_context)

        if self.recent_coder_results:
            parts.append(
                "Recent coder/tool results:\n"
                + json.dumps(self.recent_coder_results, separators=(",", ":"))
            )

        parts.append(f"Current user request:\n{self.user_message}")
        return "\n\n".join(parts)


class AiderAgentLoop:
    """Thin multi-step agent loop for orchestrating one primary aider coding tool."""

    def __init__(self, *, coder, callback: Optional[Callable[[str, dict], Awaitable[None]]] = None, config: Optional[AgentLoopConfig] = None, memory: Optional[AgentMemory] = None):
        self.coder = coder
        self.callback = callback
        self.config = config or AgentLoopConfig()
        repo_root = getattr(getattr(self.coder, "repo", None), "root", None)
        self.memory = memory or AgentMemory(session_id="default", repo_root=repo_root)
        self.editor_coder = self._build_editor_coder()
        self.architect_coder = self._build_architect_coder()
        self.tool_registry = ToolRegistry()
        self.tool_registry.register(
            Tool(
                name="aider_coder",
                description="Use Aider to plan and make code changes in the repository",
                func=self._run_architect_then_editor,
                parameters={
                    "type": "function",
                    "function": {
                        "name": "aider_coder",
                        "description": "Run aider headless coder on a natural language coding task.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string"},
                                "constraints": {"type": "string"},
                                "include_diff": {"type": "boolean"},
                            },
                            "required": ["task"],
                        },
                    },
                },
            )
        )
        self.tool_registry.register(Tool(name="file_read", description="Read a text file from the repository", func=self._tool_file_read, artifact_type="file"))
        self.tool_registry.register(Tool(name="file_grep", description="Search text pattern in repository files", func=self._tool_file_grep, artifact_type="text"))
        self.tool_registry.register(Tool(name="list_tree", description="List files in repository tree", func=self._tool_list_tree, artifact_type="text"))
        self.tool_registry.register(Tool(name="run_shell", description="Run approved test/lint/build shell command", func=self._tool_run_shell, artifact_type="shell", permission_tier="risky", required_reaction="approve_shell"))
        self.tool_registry.register(Tool(name="task_complete", description="Signal task completion with final summary", func=self._tool_task_complete, artifact_type="text"))

    def _build_editor_coder(self):
        if not self.config.editor_model:
            return self.coder
        return self.coder.clone(main_model=models.Model(self.config.editor_model))

    def _build_architect_coder(self):
        kwargs: dict[str, Any] = {
            "edit_format": "ask",
            "map_tokens": 0,
            "suggest_shell_commands": False,
            "cache_prompts": False,
            "num_cache_warming_pings": 0,
        }
        if self.config.architect_model:
            kwargs["main_model"] = models.Model(self.config.architect_model)
        return self.coder.clone(**kwargs)

    async def _emit(self, event_name: str, payload: dict):
        if self.callback:
            await self.callback(event_name, payload)

    def _build_repo_context(self) -> dict:
        repo = self.coder.repo
        if not repo or not getattr(repo, "root", None):
            return {"repo_root": None, "tracked_files": [], "git_status": ""}

        root = Path(repo.root)
        tracked_files = []
        try:
            tracked_files = sorted([str(Path(f)) for f in repo.get_tracked_files()])[: self.config.max_repo_files]
        except Exception:
            tracked_files = []

        git_status = ""
        try:
            git_status = repo.repo.git.status("--short")
        except Exception:
            git_status = ""

        return {
            "repo_root": str(root),
            "tracked_files": tracked_files,
            "git_status": git_status,
        }

    def _build_history_context(self) -> List[dict]:
        history = []
        for msg in (self.coder.done_messages or [])[-6:]:
            history.append({"role": msg.get("role"), "content": msg.get("content", "")[:1000]})
        return history

    def _build_coder_results_context(self) -> List[dict]:
        coder_results = []
        for msg in (self.coder.done_messages or [])[-10:]:
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not content:
                continue
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    coder_results.append(parsed)
                else:
                    coder_results.append({"raw": str(parsed)[:2000]})
            except (json.JSONDecodeError, TypeError):
                coder_results.append({"raw": str(content)[:2000]})
        return coder_results[-3:]

    def _system_prompt(self) -> str:
        return (
            "You are an autonomous software development orchestration agent. "
            "For coding tasks, invoke aider_coder so work executes in two phases: "
            "Architect planning first, then Editor implementation. "
            "Prefer explicit assumptions, short plans, and iterative execution. "
            "When uncertain, ask for clarification before editing."
        )

    def build_context(self, user_message: str) -> AgentContext:
        running_context = self.memory.build_running_context(user_intent=user_message)
        return AgentContext(
            system_prompt=self._system_prompt(),
            user_message=user_message,
            recent_conversation=self._build_history_context(),
            recent_coder_results=self._build_coder_results_context(),
            repository=self._build_repo_context(),
            project_instructions=getattr(self.coder, "main_system", ""),
            running_context=running_context,
        )

    async def _call_llm(self, messages: list[dict]) -> Any:
        extra_params = dict(getattr(self.coder.main_model, "extra_params", {}) or {})
        kwargs = {
            "model": self.coder.main_model.name,
            "messages": messages,
            "tools": self.tool_registry.get_tool_definitions(),
            "tool_choice": "auto",
            "temperature": 0,
            **extra_params,
        }
        return await asyncio.to_thread(litellm.completion, **kwargs)

    async def run(self, user_message: str) -> Dict[str, Any]:
        self.memory.add_working_turn(role="user", content=user_message, meta={"kind": "request"})
        last_coder_result = None
        done = False
        for idx in range(max(1, self.config.max_iterations)):
            if done:
                break
            context = self.build_context(user_message)
            await self._emit("context_built", {"context": asdict(context), "iteration": idx + 1})
            user_turn_content = context.get_user_turn_content()
            await self._emit("thinking", {"iteration": idx + 1})
            messages = [
                {"role": "system", "content": context.system_prompt},
                {"role": "user", "content": user_turn_content},
            ]
            completion = await self._call_llm(messages)
            message = completion.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []

            if not tool_calls:
                final_text = message.content or ""
                self.memory.add_working_turn(role="assistant", content=final_text, meta={"kind": "final"})
                self.memory.add_episodic_summary(summary=final_text, metadata={"agent_iterations": idx + 1})
                await self._emit("response_complete", {"iteration": idx + 1})
                done = True
                return {
                    "summary": final_text,
                    "agent_iterations": idx + 1,
                    "coder_result": last_coder_result,
                }

            handled_tool = False
            for call in tool_calls:
                args = json.loads(call.function.arguments or "{}")
                exec_args = args
                if call.function.name == "aider_coder":
                    task = args.get("task", "")
                    constraints = args.get("constraints", "")
                    include_diff = bool(args.get("include_diff", False))
                    composed_task = task if not constraints else f"{task}\n\nConstraints:\n{constraints}"
                    exec_args = {
                        "task": composed_task,
                        "include_diff": include_diff,
                        "iteration": idx + 1,
                    }
                try:
                    tool_result = await self.tool_registry.execute(call.function.name, exec_args)
                except ValueError:
                    continue
                handled_tool = True
                last_coder_result = tool_result.to_dict()
                if call.function.name == "task_complete" and tool_result.status == "ok":
                    summary = str((tool_result.data or {}).get("summary", "Task complete"))
                    self.memory.add_episodic_summary(summary=summary, metadata={"agent_iterations": idx + 1, "completed_by_tool": True})
                    await self._emit("response_complete", {"iteration": idx + 1, "reason": "task_complete"})
                    done = True
                    return {"summary": summary, "agent_iterations": idx + 1, "coder_result": last_coder_result}

                if call.function.name == "run_shell" and tool_result.status == "ok":
                    shell_data = tool_result.data or {}
                    if isinstance(shell_data, dict) and int(shell_data.get("returncode", 0)) != 0:
                        self.memory.flag_error("Test failed")
                self.memory.add_working_turn(
                    role="tool",
                    content=json.dumps(last_coder_result, separators=(",", ":"))[:2000],
                    meta={"tool": call.function.name, "iteration": idx + 1},
                )
                break

            if not handled_tool:
                break

        await self._emit("response_complete", {"iteration": self.config.max_iterations, "reason": "max_iterations"})
        self.memory.add_episodic_summary(
            summary="Completed agent loop iterations.",
            metadata={"agent_iterations": min(self.config.max_iterations, 3)},
        )
        return {
            "summary": "Completed agent loop iterations.",
            "agent_iterations": min(self.config.max_iterations, 3),
            "coder_result": last_coder_result,
        }

    def _repo_root(self) -> Path:
        repo = self.coder.repo
        root = getattr(repo, "root", None)
        return Path(root) if root else Path.cwd()

    def _safe_repo_path(self, rel_path: str) -> Path:
        root = self._repo_root().resolve()
        candidate = (root / rel_path).resolve()
        if root not in [candidate, *candidate.parents]:
            raise ValueError("Path escapes repository root")
        return candidate

    def _tool_file_read(self, path: str, start_line: int = 1, max_lines: int = 300) -> dict:
        file_path = self._safe_repo_path(path)
        text = file_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = max(1, start_line)
        end = min(len(lines), start + max(1, max_lines) - 1)
        excerpt = "\n".join(lines[start - 1 : end])
        return {"path": path, "start_line": start, "end_line": end, "content": excerpt}

    def _tool_file_grep(self, pattern: str, max_matches: int = 100) -> dict:
        root = self._repo_root()
        rx = re.compile(pattern)
        matches = []
        for p in root.rglob("*"):
            if len(matches) >= max_matches:
                break
            if not p.is_file() or ".git" in p.parts:
                continue
            try:
                for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
                    if rx.search(line):
                        matches.append({"path": str(p.relative_to(root)), "line": lineno, "text": line[:400]})
                        if len(matches) >= max_matches:
                            break
            except Exception:
                continue
        return {"pattern": pattern, "matches": matches}

    def _tool_list_tree(self, path: str = ".", max_entries: int = 500) -> dict:
        base = self._safe_repo_path(path)
        root = self._repo_root()
        entries = []
        for p in sorted(base.rglob("*")):
            if len(entries) >= max_entries:
                break
            if ".git" in p.parts:
                continue
            entries.append(str(p.relative_to(root)))
        return {"base": path, "entries": entries}

    async def _tool_run_shell(self, command: str, timeout_seconds: int = 120) -> dict:
        allowed_prefixes = [("pytest",), ("npm", "test"), ("flake8",), ("ruff", "check"), ("python", "-m", "pytest")]
        blocked_fragments = ["rm -rf", "curl | sh", "wget | sh", ":(){", "mkfs", "shutdown"]
        lowered = command.lower()
        if any(b in lowered for b in blocked_fragments):
            raise ValueError("Blocked command pattern")

        parts = shlex.split(command)
        prefix = tuple(parts[:3])
        if not any(prefix[: len(ap)] == ap for ap in allowed_prefixes):
            raise ValueError("Command is not on the allowlist")

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self._repo_root()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        return {
            "command": command,
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace")[:6000],
            "stderr": stderr.decode("utf-8", errors="replace")[:6000],
        }

    def _tool_task_complete(self, summary: str) -> dict:
        return {"summary": summary}

    async def _run_architect_then_editor(self, *, task: str, include_diff: bool, iteration: int):
        if not self.config.use_architect_mode:
            await self._emit("executing_edits", {"iteration": iteration, "task": task})
            return await self.editor_coder.run_structured_async(task, preproc=True, include_diff=include_diff)

        await self._emit("planning_with_architect", {"iteration": iteration, "task": task})
        architect_prompt = (
            "Create a concise, implementation-ready plan for the editor. "
            "List assumptions, target files, and step-by-step edits.\n\n"
            f"User request:\n{task}"
        )
        plan_result = await self.architect_coder.run_structured_async(
            architect_prompt,
            preproc=True,
            include_diff=False,
        )
        plan_text = plan_result.summary or ""

        editor_instruction = (
            f"Original request:\n{task}\n\n"
            f"Architect plan/proposal:\n{plan_text}\n\n"
            "Implement the request faithfully following the plan."
        )
        await self._emit("executing_edits", {"iteration": iteration, "task": task, "plan": plan_text})
        return await self.editor_coder.run_structured_async(
            editor_instruction,
            preproc=True,
            include_diff=include_diff,
        )
