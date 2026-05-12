from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyTask, Deliverable
from aider.memory import ProjectMemory


@dataclass
class COOMessage:
    """Message envelope used by the local Nanobot-inspired COO bus."""

    channel: str
    session_key: str
    content: str
    role: str = "user"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class COOMessageBus:
    """Small async bus that decouples user channels from company orchestration."""

    def __init__(self):
        self.inbound: asyncio.Queue[COOMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[COOMessage] = asyncio.Queue()

    async def publish_inbound(self, message: COOMessage) -> None:
        await self.inbound.put(message)

    async def consume_inbound(self) -> COOMessage:
        return await self.inbound.get()

    async def publish_outbound(self, message: COOMessage) -> None:
        await self.outbound.put(message)

    async def consume_outbound(self) -> COOMessage:
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        return self.outbound.qsize()


@dataclass
class COOSession:
    """Durable conversation/session state for one user/channel thread."""

    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def add_message(self, role: str, content: str, **metadata: Any) -> None:
        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
                **metadata,
            }
        )
        self.updated_at = datetime.utcnow().isoformat()

    def get_history(self, max_messages: int = 40) -> list[dict[str, Any]]:
        return self.messages[-max(1, max_messages) :]


class COOSessionManager:
    """JSONL session store modeled after Nanobot's lightweight session manager."""

    def __init__(self, project_memory: ProjectMemory, dirname: str = "coo_sessions"):
        self.project_memory = project_memory
        self.sessions_dir = Path(project_memory.repo_path) / ".aider" / dirname
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, COOSession] = {}

    @staticmethod
    def safe_key(key: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in key)
        return safe[:160] or "default"

    def _path(self, key: str) -> Path:
        return self.sessions_dir / f"{self.safe_key(key)}.jsonl"

    def get_or_create(self, key: str) -> COOSession:
        if key in self._cache:
            return self._cache[key]
        session = self._load(key) or COOSession(key=key)
        self._cache[key] = session
        return session

    def _load(self, key: str) -> Optional[COOSession]:
        path = self._path(key)
        if not path.exists():
            return None
        messages: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {}
        created_at: Optional[str] = None
        updated_at: Optional[str] = None
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("_type") == "metadata":
                    metadata = payload.get("metadata", {}) or {}
                    created_at = payload.get("created_at")
                    updated_at = payload.get("updated_at")
                else:
                    messages.append(payload)
        return COOSession(
            key=key,
            messages=messages,
            metadata=metadata,
            created_at=created_at or datetime.utcnow().isoformat(),
            updated_at=updated_at or datetime.utcnow().isoformat(),
        )

    def save(self, session: COOSession) -> None:
        path = self._path(session.key)
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "_type": "metadata",
                        "key": session.key,
                        "created_at": session.created_at,
                        "updated_at": session.updated_at,
                        "metadata": session.metadata,
                    },
                    ensure_ascii=False,
                )
                + os.linesep
            )
            for message in session.messages:
                handle.write(json.dumps(message, ensure_ascii=False) + os.linesep)
        os.replace(tmp, path)
        self._cache[session.key] = session

    def flush_all(self) -> int:
        for session in list(self._cache.values()):
            self.save(session)
        return len(self._cache)


class NanobotCOO:
    """Persistent COO agent that mediates users, sessions, and departments.

    This intentionally clones Nanobot's useful primitives locally: an async message
    bus, durable session manager, and a small persistent agent-loop facade. It does
    not import or bridge to Nanobot at runtime.
    """

    def __init__(
        self,
        *,
        orchestrator: CompanyOrchestrator,
        agent_loop=None,
        session_manager: COOSessionManager | None = None,
        bus: COOMessageBus | None = None,
        default_target: str = "product",
        enable_llm_routing: bool | None = None,
    ):
        self.orchestrator = orchestrator
        self.agent_loop = agent_loop
        self.session_manager = session_manager or COOSessionManager(orchestrator.memory)
        self.bus = bus or COOMessageBus()
        self.default_target = default_target
        self.enable_llm_routing = (
            orchestrator.company_config.enable_coo_llm_routing
            if enable_llm_routing is None
            else enable_llm_routing
        )

    async def receive_user_message(
        self,
        *,
        prompt: str,
        channel: str,
        session_key: str,
        target: str | None = None,
        artifact_type: str = "raw_prompt",
        context: dict[str, Any] | None = None,
        blocking: bool = False,
        wait: bool = True,
        task_id: str | None = None,
        origin: str | None = None,
    ) -> Optional[Deliverable]:
        """Persist a user turn, choose a department, and hand off the task."""
        message = COOMessage(
            channel=channel,
            session_key=session_key,
            content=prompt,
            metadata={"target": target, "artifact_type": artifact_type},
        )
        await self.bus.publish_inbound(message)
        return await self._handle_inbound(
            await self.bus.consume_inbound(),
            target=target,
            artifact_type=artifact_type,
            context=context,
            blocking=blocking,
            wait=wait,
            task_id=task_id,
            origin=origin,
        )

    async def _handle_inbound(
        self,
        message: COOMessage,
        *,
        target: str | None,
        artifact_type: str,
        context: dict[str, Any] | None,
        blocking: bool,
        wait: bool,
        task_id: str | None,
        origin: str | None,
    ) -> Optional[Deliverable]:
        session = self.session_manager.get_or_create(message.session_key)
        session.add_message(
            "user",
            message.content,
            channel=message.channel,
            metadata=message.metadata,
        )
        resolved_target = target or await self.resolve_target(message.content, session)
        task = CompanyTask(
            task_id=task_id or str(uuid.uuid4()),
            origin=origin or message.channel,
            target=resolved_target,
            artifact_type=artifact_type,
            payload=message.content,
            blocking=blocking,
            context=context or {},
        )
        session.metadata.update(
            {
                "last_task_id": task.task_id,
                "last_target": resolved_target,
                "last_channel": message.channel,
            }
        )
        self.session_manager.save(session)

        if wait:
            deliverable = await self.run_department_task(task)
            session.add_message(
                "assistant",
                self._summarize_deliverable(deliverable),
                department=deliverable.department,
                status=deliverable.status,
                task_id=deliverable.task_id,
            )
            self.session_manager.save(session)
            await self.bus.publish_outbound(
                COOMessage(
                    channel=message.channel,
                    session_key=message.session_key,
                    role="assistant",
                    content=self._summarize_deliverable(deliverable),
                    metadata={"task_id": task.task_id, "department": deliverable.department},
                )
            )
            return deliverable

        await self.orchestrator.submit(task)
        return None

    async def run_department_task(self, task: CompanyTask) -> Deliverable:
        """Run one department synchronously and route the produced deliverable."""
        if task.target not in self.orchestrator.departments:
            raise ValueError(f"No department: {task.target}")
        department = self.orchestrator.departments[task.target]
        task.context = self.orchestrator.context_builder.build(
            task,
            department.get_context_requirements(),
            self.orchestrator.active_project,
        )
        deliverable = await department.process(task)
        await self.orchestrator._route(deliverable)
        return deliverable

    async def resolve_target(self, prompt: str, session: COOSession) -> str:
        """Choose the target department, optionally using the COO's own agent loop."""
        if self.enable_llm_routing and self.agent_loop is not None:
            result = await self.agent_loop.run_structured(
                task=json.dumps(
                    {
                        "prompt": prompt,
                        "history": session.get_history(12),
                        "departments": sorted(self.orchestrator.departments),
                    },
                    ensure_ascii=False,
                ),
                system_prompt=(
                    "You are the COO routing user work to one department. "
                    "Return only JSON like {\"target\": \"product\"}. "
                    "Valid targets: product, ux, engineering, qa, devops."
                ),
            )
            parsed = self._parse_json(result.get("content", ""))
            candidate = str(parsed.get("target", "")).lower()
            if candidate in self.orchestrator.departments:
                return candidate

        if self.orchestrator.active_project is not None:
            project = self.orchestrator.active_project
            if project.phase == "prototyping" and not project.prd:
                return "product"
        return "engineering" if "engineering" in self.orchestrator.departments else self.default_target

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        try:
            parsed = json.loads(content.strip())
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _summarize_deliverable(deliverable: Deliverable) -> str:
        if isinstance(deliverable.payload, str):
            return deliverable.payload
        return json.dumps(deliverable.payload, ensure_ascii=False, default=str)
