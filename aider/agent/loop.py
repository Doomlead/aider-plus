from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from aider.memory import ConversationMemory, Message, ProjectMemory
from aider.onboarding import onboarding_paths

from aider.llm import litellm
from aider import models
from aider.agent.tools import Tool, ToolPermissionError, ToolRegistry
from aider.mcp import MCPClientManager, MCPConfig, mcp_tool_to_aider_tool


@dataclass
class AgentLoopConfig:
    max_iterations: int = 3
    max_repo_files: int = 50
    use_architect_mode: bool = True
    architect_model: str | None = None
    editor_model: str | None = None
    reviewer_model: str | None = None
    enable_caching: bool = True
    cache_type: Literal["auto", "prompt", "none"] = "auto"
    mcp: MCPConfig | None = None


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

    def __init__(
        self,
        *,
        coder,
        callback: Optional[Callable[[str, dict], Awaitable[None]]] = None,
        config: Optional[AgentLoopConfig] = None,
        tool_registry: ToolRegistry | None = None,
        mcp_manager: MCPClientManager | None = None,
        enable_prompt_caching: bool | None = None,
        cache_type: Literal["auto", "prompt", "none"] | None = None,
    ):
        self.coder = coder
        self.callback = callback
        self.config = config or AgentLoopConfig()
        if enable_prompt_caching is not None:
            self.config.enable_caching = bool(enable_prompt_caching)
        if cache_type is not None:
            self.config.cache_type = cache_type
        if self.config.cache_type == "none":
            self.config.enable_caching = False
        self._ensure_onboarded_state()
        self.editor_coder = self._build_editor_coder()
        self.architect_coder = self._build_architect_coder()
        self.tool_registry = tool_registry or ToolRegistry()
        self.mcp_manager = mcp_manager
        self.mcp_approval_handler = None
        self._mcp_initialized_scopes: set[str] = set()
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

    @property
    def default_model(self) -> str:
        return self.coder.main_model.name

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

    @property
    def enable_prompt_caching(self) -> bool:
        """Backward-compatible alias for agent-loop caching."""
        return self.config.enable_caching

    @enable_prompt_caching.setter
    def enable_prompt_caching(self, value: bool) -> None:
        self.config.enable_caching = bool(value)
        if not self.config.enable_caching:
            self.config.cache_type = "none"
        elif self.config.cache_type == "none":
            self.config.cache_type = "auto"

    def _resolve_cache_enabled(self, enable_caching: bool | None = None) -> bool:
        if enable_caching is not None:
            return bool(enable_caching)
        if self.config.cache_type == "none":
            return False
        return bool(self.config.enable_caching)

    def _apply_cache_control(self, messages):
        """Return messages unchanged while respecting native cache-control markers.

        Aider's core Coder owns message formatting and any provider-specific
        cache-control content markers. The company loop only toggles request-level
        cache options, so this method intentionally avoids mutating message
        payloads and preserves existing cache prefixes/controls.
        """
        if not self.config.enable_caching or self.config.cache_type == "none":
            return messages
        return messages

    def _apply_cache_request_options(
        self,
        kwargs: dict[str, Any],
        enable_caching: bool | None = None,
    ) -> dict[str, Any]:
        """Merge high-level prompt caching controls into LLM kwargs."""
        cache_enabled = self._resolve_cache_enabled(enable_caching)
        kwargs["cache_prompts"] = cache_enabled
        if not cache_enabled:
            extra_body = dict(kwargs.pop("extra_body", None) or {})
            extra_body.pop("cache_control", None)
            if extra_body:
                kwargs["extra_body"] = extra_body
            return kwargs

        extra_body = dict(kwargs.pop("extra_body", None) or {})
        extra_body.setdefault("cache_control", {"type": "ephemeral"})
        kwargs["extra_body"] = extra_body
        return kwargs

    async def _call_llm(
        self,
        messages: list[dict] | None = None,
        *,
        task: str | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        enable_caching: bool | None = None,
        enable_prompt_caching: bool | None = None,
        **kwargs,
    ) -> Any:
        if messages is None:
            messages = [
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": task or ""},
            ]
        cache_override = (
            enable_prompt_caching
            if enable_prompt_caching is not None
            else enable_caching
        )
        messages = self._apply_cache_control(messages)
        extra_params = dict(getattr(self.coder.main_model, "extra_params", {}) or {})
        request_kwargs = {
            "model": model or self.coder.main_model.name,
            "messages": messages,
            "tools": self.tool_registry.get_tool_definitions(),
            "tool_choice": "auto",
            "temperature": 0,
            **extra_params,
            **kwargs,
        }
        request_kwargs = self._apply_cache_request_options(request_kwargs, cache_override)
        return await asyncio.to_thread(litellm.completion, **request_kwargs)

    async def run_structured(
        self,
        *,
        task: str,
        system_prompt: str,
        model: str | None = None,
        enable_caching: bool | None = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Run a structured, non-editing LLM task through the agent loop."""
        selected_model = model or self.default_model
        await self._emit("structured_review_start", {"model": selected_model})
        completion = await self._call_llm(
            task=task,
            system_prompt=system_prompt,
            model=selected_model,
            enable_prompt_caching=self._resolve_cache_enabled(enable_caching),
            tools=None,
            tool_choice=None,
            **kwargs,
        )
        message = completion.choices[0].message
        content = getattr(message, "content", None) or ""
        await self._emit("structured_review_complete", {"model": selected_model})
        return {"content": content, "model": selected_model}

    async def run(
        self,
        user_message: str,
        *,
        enable_caching: bool | None = None,
    ) -> Dict[str, Any]:
        conversation_memory = getattr(self.coder, "conversation_memory", None)
        if isinstance(conversation_memory, ConversationMemory):
            conversation_memory.add(role="user", content=user_message)

        context = self.build_context(user_message)
        await self._initialize_mcp_tools()
        await self._emit("context_built", {"context": asdict(context)})

        user_turn_content = context.get_user_turn_content()
        last_coder_result = None
        for idx in range(max(1, min(self.config.max_iterations, 3))):
            await self._emit("thinking", {"iteration": idx + 1})
            messages = [
                {"role": "system", "content": context.system_prompt},
                {"role": "user", "content": user_turn_content},
            ]
            completion = await self._call_llm(messages, enable_caching=enable_caching)
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
                exec_args = self._tool_call_arguments(call.function.name, args, idx + 1)
                try:
                    coder_result = await self.tool_registry.execute(call.function.name, exec_args)
                except ToolPermissionError as err:
                    await self._emit("permission_violation", err.to_dict())
                    return {
                        "summary": err.to_dict()["message"],
                        "agent_iterations": idx + 1,
                        "coder_result": None,
                        "error": err.to_dict(),
                    }
                except ValueError:
                    continue
                handled_tool = True
                last_coder_result = self._tool_result_to_dict(coder_result)
                break

            if not handled_tool:
                break

        await self._emit("response_complete", {"iteration": self.config.max_iterations, "reason": "max_iterations"})
        return {
            "summary": "Completed agent loop iterations.",
            "agent_iterations": min(self.config.max_iterations, 3),
            "coder_result": last_coder_result,
        }

    async def _initialize_mcp_tools(self) -> None:
        mcp_config = self.config.mcp
        if self.mcp_manager is None and mcp_config is not None and mcp_config.enabled:
            self.mcp_manager = MCPClientManager(
                mcp_config, approval_handler=self.mcp_approval_handler
            )
        if self.mcp_manager is None or not self.mcp_manager.config.enabled:
            return
        if self.mcp_approval_handler is not None:
            self.mcp_manager.approval_handler = self.mcp_approval_handler
        project_dir = getattr(getattr(self.coder, "root", None), "as_posix", lambda: None)()
        if project_dir is None:
            project_dir = str(getattr(self.coder, "root", "") or "")
        task_dir = project_dir
        scope_key = f"{project_dir}:{task_dir}"
        if scope_key in self._mcp_initialized_scopes:
            return
        tools = await self.mcp_manager.list_tools(
            project_dir=project_dir, task_dir=task_dir, scope_key=scope_key
        )
        for tool_ref in tools:
            self.tool_registry.register(mcp_tool_to_aider_tool(self.mcp_manager, tool_ref))
        self._mcp_initialized_scopes.add(scope_key)

    @staticmethod
    def _tool_call_arguments(name: str, args: dict, iteration: int) -> dict:
        if name != "aider_coder":
            return args
        task = args.get("task", "")
        constraints = args.get("constraints", "")
        include_diff = bool(args.get("include_diff", False))
        composed_task = task if not constraints else f"{task}\n\nConstraints:\n{constraints}"
        return {"task": composed_task, "include_diff": include_diff, "iteration": iteration}

    @staticmethod
    def _tool_result_to_dict(result: Any) -> dict:
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        return {"result": result}

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
