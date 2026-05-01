from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aider.llm import litellm


@dataclass
class AgentLoopConfig:
    max_iterations: int = 3
    max_repo_files: int = 50


@dataclass
class AgentContext:
    """Structured context bundle consumed by the LLM call."""

    system_prompt: str
    user_message: str
    recent_conversation: list[dict]
    recent_coder_results: list[dict]
    repository: dict[str, Any]
    project_instructions: str

    def as_messages_for_coder(self, coder) -> list[dict]:
        """Build cache-friendly messages using coder-native prefix construction."""
        dynamic_context = {
            "recent_conversation": self.recent_conversation,
            "recent_coder_results": self.recent_coder_results,
            "project_instructions": self.project_instructions,
        }
        user_content = (
            f"User request:\n{self.user_message}\n\n"
            f"Recent dynamic context (non-cacheable):\n{json.dumps(dynamic_context)}"
        )

        original_cur = list(getattr(coder, "cur_messages", []) or [])
        original_done = list(getattr(coder, "done_messages", []) or [])
        original_sys = getattr(coder, "main_system", "")
        try:
            # Build Aider's stable/cacheable prefix using native chunk formatting.
            coder.main_system = self.system_prompt
            coder.done_messages = []
            coder.cur_messages = []
            prefix_chunks = coder.format_messages()
            prefix_messages = (
                prefix_chunks.system
                + prefix_chunks.examples
                + prefix_chunks.readonly_files
                + prefix_chunks.repo
                + prefix_chunks.chat_files
            )

            # Keep dynamic content after the cache-friendly prefix.
            dynamic_messages = list(self.recent_conversation)
            dynamic_messages.append({"role": "user", "content": user_content})
            return prefix_messages + dynamic_messages
        finally:
            coder.main_system = original_sys
            coder.cur_messages = original_cur
            coder.done_messages = original_done


class AiderAgentLoop:
    """Thin multi-step agent loop for orchestrating one primary aider coding tool."""

    def __init__(self, *, coder, callback: Optional[Callable[[str, dict], Awaitable[None]]] = None, config: Optional[AgentLoopConfig] = None):
        self.coder = coder
        self.callback = callback
        self.config = config or AgentLoopConfig()

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
            "You are an autonomous software development company agent. "
            "You can reply directly, ask clarifying questions, or call the aider_coder tool "
            "to prototype and implement changes. Prefer short plans, explicit assumptions, and iterative execution. "
            "When uncertain, ask for clarification before editing."
        )

    def build_context(self, user_message: str) -> AgentContext:
        return AgentContext(
            system_prompt=self._system_prompt(),
            user_message=user_message,
            recent_conversation=self._build_history_context(),
            recent_coder_results=self._build_coder_results_context(),
            repository=self._build_repo_context(),
            project_instructions=getattr(self.coder, "main_system", ""),
        )

    def _tools(self) -> list[dict]:
        return [
            {
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
            }
        ]

    async def _call_llm(self, messages: list[dict]) -> Any:
        extra_params = dict(getattr(self.coder.main_model, "extra_params", {}) or {})
        kwargs = {
            "model": self.coder.main_model.name,
            "messages": messages,
            "tools": self._tools(),
            "tool_choice": "auto",
            "temperature": 0,
            **extra_params,
        }
        return await asyncio.to_thread(litellm.completion, **kwargs)

    async def run(self, user_message: str) -> Dict[str, Any]:
        context = self.build_context(user_message)
        await self._emit("context_built", {"context": asdict(context)})

        messages: list[dict] = context.as_messages_for_coder(self.coder)

        last_coder_result = None
        for idx in range(max(1, min(self.config.max_iterations, 3))):
            await self._emit("thinking", {"iteration": idx + 1})
            completion = await self._call_llm(messages)
            message = completion.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []

            if not tool_calls:
                final_text = message.content or ""
                await self._emit("response_complete", {"iteration": idx + 1})
                return {
                    "summary": final_text,
                    "agent_iterations": idx + 1,
                    "coder_result": last_coder_result,
                }

            handled_tool = False
            for call in tool_calls:
                if call.function.name != "aider_coder":
                    continue
                handled_tool = True
                args = json.loads(call.function.arguments or "{}")
                task = args.get("task", "")
                constraints = args.get("constraints", "")
                include_diff = bool(args.get("include_diff", False))
                composed_task = task if not constraints else f"{task}\n\nConstraints:\n{constraints}"

                await self._emit("applying_edits", {"iteration": idx + 1, "task": task})
                coder_result = await self.coder.run_structured_async(
                    composed_task,
                    preproc=True,
                    include_diff=include_diff,
                )
                last_coder_result = coder_result.to_dict()

                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": "aider_coder",
                    "content": json.dumps(last_coder_result),
                }
                messages.append({"role": "assistant", "content": message.content or "", "tool_calls": tool_calls})
                messages.append(tool_message)
                break

            if not handled_tool:
                break

        await self._emit("response_complete", {"iteration": self.config.max_iterations, "reason": "max_iterations"})
        return {
            "summary": "Completed agent loop iterations.",
            "agent_iterations": min(self.config.max_iterations, 3),
            "coder_result": last_coder_result,
        }
