from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aider.llm import litellm


@dataclass
class AgentLoopConfig:
    max_iterations: int = 3
    max_repo_files: int = 50


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

    def _system_prompt(self) -> str:
        return (
            "You are an autonomous software development company agent. "
            "You can reply directly, ask clarifying questions, or call the aider_coder tool "
            "to prototype and implement changes. Prefer short plans and iterative execution. "
            "When uncertain, ask for clarification before editing."
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
        kwargs = {
            "model": self.coder.main_model.name,
            "messages": messages,
            "tools": self._tools(),
            "tool_choice": "auto",
            "temperature": 0,
        }
        return await asyncio.to_thread(litellm.completion, **kwargs)

    async def run(self, user_message: str) -> Dict[str, Any]:
        repo_context = self._build_repo_context()
        history_context = self._build_history_context()
        instructions = getattr(self.coder, "main_system", "")

        context_blob = {
            "repo": repo_context,
            "recent_history": history_context,
            "project_instructions": instructions,
        }

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": f"Context:\n{json.dumps(context_blob)}\n\nUser request:\n{user_message}"},
        ]

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
