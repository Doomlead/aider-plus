from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aider.memory import ConversationMemory, Message, ProjectMemory
from aider.onboarding import onboarding_paths

from aider.llm import litellm
from aider import models
from aider.agent.tools import Tool, ToolRegistry


@dataclass
class AgentLoopConfig:
    max_iterations: int = 3
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
    conversation_buffer: list[Message]
    project_memory: dict[str, Any]


    def get_user_turn_content(self) -> str:
        """Return a single user-turn payload for the current loop step."""
        parts: list[str] = []

        recent = self.conversation_buffer or self.recent_conversation
        if recent:
            convo_lines = []
            for msg in recent[-4:]:
                role = msg.get("role", "unknown")
                content = str(msg.get("content", "")).strip()
                if content:
                    convo_lines.append(f"- {role}: {content[:400]}")
            if convo_lines:
                parts.append("Recent conversation:\n" + "\n".join(convo_lines))

        if self.recent_coder_results:
            parts.append(
                "Recent coder/tool results:\n"
                + json.dumps(self.recent_coder_results, separators=(",", ":"))
            )

        if self.project_memory:
            parts.append("Project memory:\n" + json.dumps(self.project_memory, separators=(",", ":")))

        parts.append(f"Current user request:\n{self.user_message}")
        return "\n\n".join(parts)


class AiderAgentLoop:
    """Thin multi-step agent loop for orchestrating one primary aider coding tool."""

    def __init__(self, *, coder, callback: Optional[Callable[[str, dict], Awaitable[None]]] = None, config: Optional[AgentLoopConfig] = None):
        self.coder = coder
        self.callback = callback
        self.config = config or AgentLoopConfig()
        self._ensure_onboarded_state()
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

    def _ensure_onboarded_state(self):
        if not getattr(self.coder, "conversation_memory", None):
            self.coder.conversation_memory = ConversationMemory()

        repo = getattr(self.coder, "repo", None)
        repo_root = getattr(repo, "root", None) if repo else None
        if not repo_root:
            return

        if not getattr(self.coder, "project_memory", None):
            pm = ProjectMemory(repo_root)
            pm.load()
            self.coder.project_memory = pm
        elif isinstance(self.coder.project_memory, ProjectMemory):
            self.coder.project_memory.load()

        cfg_exists = onboarding_paths()["config_path"].exists() or Path(".aider.conf.yml").exists()
        if not cfg_exists:
            print("Tip: run `aider onboard` for guided Company setup.")

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
        conversation_memory = getattr(self.coder, "conversation_memory", None)
        project_memory = getattr(self.coder, "project_memory", None)

        conversation_buffer = conversation_memory.get() if isinstance(conversation_memory, ConversationMemory) else self._build_history_context()
        project_state = project_memory.data if isinstance(project_memory, ProjectMemory) else {}

        return AgentContext(
            system_prompt=self._system_prompt(),
            user_message=user_message,
            recent_conversation=self._build_history_context(),
            recent_coder_results=self._build_coder_results_context(),
            repository=self._build_repo_context(),
            project_instructions=getattr(self.coder, "main_system", ""),
            conversation_buffer=conversation_buffer,
            project_memory=project_state,
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
        conversation_memory = getattr(self.coder, "conversation_memory", None)
        if isinstance(conversation_memory, ConversationMemory):
            conversation_memory.add(role="user", content=user_message)

        context = self.build_context(user_message)
        await self._emit("context_built", {"context": asdict(context)})

        user_turn_content = context.get_user_turn_content()
        last_coder_result = None
        for idx in range(max(1, min(self.config.max_iterations, 3))):
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
                await self._emit("response_complete", {"iteration": idx + 1})
                if isinstance(conversation_memory, ConversationMemory):
                    conversation_memory.add(role="assistant", content=final_text)
                return {
                    "summary": final_text,
                    "agent_iterations": idx + 1,
                    "coder_result": last_coder_result,
                }

            handled_tool = False
            for call in tool_calls:
                args = json.loads(call.function.arguments or "{}")
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
                    coder_result = await self.tool_registry.execute(call.function.name, exec_args)
                except ValueError:
                    continue
                handled_tool = True
                last_coder_result = coder_result.to_dict()
                break

            if not handled_tool:
                break

        await self._emit("response_complete", {"iteration": self.config.max_iterations, "reason": "max_iterations"})
        return {
            "summary": "Completed agent loop iterations.",
            "agent_iterations": min(self.config.max_iterations, 3),
            "coder_result": last_coder_result,
        }

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
