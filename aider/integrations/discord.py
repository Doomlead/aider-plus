"""Discord front-end helpers for running Aider in headless scripting mode.

This module keeps discord.py optional so core aider installs do not require it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional, Set

from aider.agent import AiderAgentLoop
from aider.agent.loop import AgentLoopConfig
from aider.coders import Coder
from aider.main import main as aider_main


@dataclass(frozen=True)
class DiscordSessionKey:
    guild_id: int
    channel_id: int
    user_id: Optional[int] = None
    repo_path: Optional[str] = None


@dataclass
class RepositoryPolicy:
    allowed_roots: Set[str] = field(default_factory=set)

    def validate(self, repo_path: str):
        resolved = str(Path(repo_path).resolve())
        if not self.allowed_roots:
            return
        for root in self.allowed_roots:
            root_resolved = str(Path(root).resolve())
            if resolved == root_resolved or resolved.startswith(root_resolved + "/"):
                return
        raise PermissionError(f"Repository path is not in the whitelist: {repo_path}")


@dataclass
class DiscordAiderConfig:
    max_runtime_seconds: int = 300
    max_prompt_chars: int = 12_000
    auto_commit: bool = False
    dry_run: bool = False
    allow_users: Set[int] = field(default_factory=set)
    deny_users: Set[int] = field(default_factory=set)
    repository_policy: RepositoryPolicy = field(default_factory=RepositoryPolicy)
    use_architect_mode: bool = True
    architect_model: Optional[str] = None
    editor_model: Optional[str] = None


class DiscordSessionManager:
    """In-memory session store keyed by channel/user/repo for easy future persistence."""

    def __init__(self):
        self._sessions: Dict[DiscordSessionKey, Coder] = {}
        self._last_used: Dict[DiscordSessionKey, float] = {}

    def get(self, key: DiscordSessionKey) -> Optional[Coder]:
        coder = self._sessions.get(key)
        if coder:
            self._last_used[key] = time.time()
        return coder

    def put(self, key: DiscordSessionKey, coder: Coder):
        self._sessions[key] = coder
        self._last_used[key] = time.time()

    def remove(self, key: DiscordSessionKey):
        self._sessions.pop(key, None)
        self._last_used.pop(key, None)

    def list_keys(self):
        return list(self._sessions.keys())


class DiscordAiderBot:
    """Async-friendly façade around Coder for Discord handlers.

    You can wire `run_instruction` into slash commands or mention handlers.
    """

    def __init__(self, config: Optional[DiscordAiderConfig] = None):
        self.config = config or DiscordAiderConfig()
        self.sessions = DiscordSessionManager()

    def check_access(self, user_id: int):
        if user_id in self.config.deny_users:
            raise PermissionError("User is blocked from running aider")
        if self.config.allow_users and user_id not in self.config.allow_users:
            raise PermissionError("User is not in the allowed user list")

    def _build_coder(self, repo_path: str, model: Optional[str] = None) -> Coder:
        argv = [
            "--headless",
            "--yes-always",
            "--no-auto-commits" if not self.config.auto_commit else "--auto-commits",
            "--dry-run" if self.config.dry_run else "--no-dry-run",
        ]
        if model:
            argv.extend(["--model", model])
        argv.append(repo_path)

        coder = aider_main(argv=argv, return_coder=True)
        if not isinstance(coder, Coder):
            raise RuntimeError("Unable to create aider coder for Discord session")
        return coder

    async def get_or_create_session(
        self,
        key: DiscordSessionKey,
        repo_path: str,
        model: Optional[str] = None,
    ) -> Coder:
        self.config.repository_policy.validate(repo_path)
        existing = self.sessions.get(key)
        if existing:
            return existing

        coder = await asyncio.to_thread(self._build_coder, repo_path, model)
        self.sessions.put(key, coder)
        return coder

    async def run_instruction(
        self,
        *,
        key: DiscordSessionKey,
        repo_path: str,
        user_id: int,
        prompt: str,
        model: Optional[str] = None,
        include_diff: bool = False,
        callback: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    ):
        self.check_access(user_id)
        if len(prompt) > self.config.max_prompt_chars:
            raise ValueError("Prompt too large")

        coder = await self.get_or_create_session(key, repo_path, model=model)

        agent = AiderAgentLoop(
            coder=coder,
            callback=callback,
            config=AgentLoopConfig(
                use_architect_mode=self.config.use_architect_mode,
                architect_model=self.config.architect_model,
                editor_model=self.config.editor_model,
            ),
        )
        task = asyncio.create_task(agent.run(prompt))

        try:
            result = await asyncio.wait_for(task, timeout=self.config.max_runtime_seconds)
        except asyncio.TimeoutError as err:
            raise TimeoutError("Aider request timed out") from err

        return result


def build_discord_client(*args, **kwargs):
    """Factory that imports discord.py lazily.

    This keeps aider importable without discord.py installed.
    """

    try:
        import discord
        from discord.ext import commands
    except ImportError as err:
        raise ImportError("Install discord.py to use Discord integrations") from err

    intents = kwargs.pop("intents", None)
    if intents is None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.message_content = True

    return commands.Bot(*args, intents=intents, **kwargs)
