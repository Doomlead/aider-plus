"""Nanobot-inspired COO agent kernel for Company Mode.

This module does not call out to HKUDS/nanobot. It keeps the same design idea
that makes nanobot attractive for a COO role: a tiny, readable agent kernel with
channel messages, session memory, explicit tools, and deterministic routing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable


@dataclass
class COOAgentConfig:
    """Configuration for the internal COO agent framework."""

    name: str = "coo"
    channel: str = "company"
    default_target: str = "product"
    max_session_messages: int = 50
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class COOMessage:
    """Small cross-department message envelope used by the COO kernel."""

    sender: str
    recipient: str
    content: Any
    message_type: str = "message"
    task_id: Optional[str] = None
    channel: str = "company"
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "sender": self.sender,
            "recipient": self.recipient,
            "type": self.message_type,
            "task_id": self.task_id,
            "content": self.content,
            "context": self.context,
            "metadata": self.metadata,
        }


class COOSessionMemory:
    """Bounded session memory for COO conversations and handoffs."""

    def __init__(self, max_messages: int = 50):
        self.max_messages = max(1, max_messages)
        self._messages: list[COOMessage] = []

    def add(self, message: COOMessage) -> None:
        self._messages.append(message)
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages :]

    def recent(self, limit: Optional[int] = None) -> list[COOMessage]:
        if limit is None:
            return list(self._messages)
        return list(self._messages[-max(0, limit) :])

    def as_dicts(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        return [message.to_dict() for message in self.recent(limit)]


@dataclass
class COOTool:
    """Tool callable exposed to the COO kernel."""

    name: str
    description: str
    func: Callable[..., Awaitable[Any] | Any]


class COOToolRegistry:
    """Minimal tool registry patterned after nanobot's small agent kernel."""

    def __init__(self):
        self._tools: dict[str, COOTool] = {}

    def register(self, tool: COOTool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    async def execute(self, name: str, **kwargs) -> Any:
        if name not in self._tools:
            raise ValueError(f"Unknown COO tool: {name}")
        result = self._tools[name].func(**kwargs)
        if hasattr(result, "__await__"):
            return await result
        return result


class COORoutingPolicy:
    """Deterministic intent-to-department router for COO tasks."""

    def __init__(self, default_target: str = "product"):
        self.default_target = default_target

    def select_target(self, task: CompanyTask) -> str:
        payload = task.payload if isinstance(task.payload, dict) else {}
        explicit = payload.get("target_department") or payload.get("target")
        if explicit:
            return str(explicit)

        text = self._task_text(task).lower()
        if any(word in text for word in ("deploy", "release", "docker", "infra")):
            return "devops"
        if any(word in text for word in ("test", "qa", "verify", "regression")):
            return "qa"
        if any(word in text for word in ("implement", "code", "fix", "bug", "refactor")):
            return "engineering"
        if any(word in text for word in ("design", "ux", "ui", "wireframe", "screen")):
            return "ux"
        return self.default_target

    @staticmethod
    def _task_text(task: CompanyTask) -> str:
        if isinstance(task.payload, dict):
            parts = [str(value) for value in task.payload.values() if value is not None]
            return " ".join(parts)
        return str(task.payload or "")


class COOAgentKernel:
    """Internal nanobot-style agent framework used by the COO department.

    The kernel owns a message channel, bounded session memory, a tiny tool
    registry, and a deterministic turn processor. It deliberately avoids any
    external nanobot process.
    """

    def __init__(
        self,
        *,
        config: Optional[COOAgentConfig] = None,
        routing_policy: Optional[COORoutingPolicy] = None,
    ):
        self.config = config or COOAgentConfig()
        self.memory = COOSessionMemory(self.config.max_session_messages)
        self.routing_policy = routing_policy or COORoutingPolicy(self.config.default_target)
        self.tools = COOToolRegistry()

    def task_to_message(self, task: CompanyTask) -> COOMessage:
        payload = task.payload if isinstance(task.payload, dict) else {"message": task.payload}
        target = self.routing_policy.select_target(task)
        return COOMessage(
            sender=task.origin,
            recipient=target,
            content=payload,
            message_type=str(payload.get("message_type") or task.artifact_type),
            task_id=task.task_id,
            channel=self.config.channel,
            context=dict(task.context or {}),
            metadata={"coo": self.config.name, **self.config.metadata},
        )

    async def process_turn(self, task: CompanyTask) -> tuple[COOMessage, Optional[Any]]:
        message = self.task_to_message(task)
        self.memory.add(message)
        result = None
        if message.recipient != self.config.name and "handoff" in self.tools.names():
            result = await self.tools.execute("handoff", task=task, message=message)
        response = COOMessage(
            sender=self.config.name,
            recipient=task.origin,
            content={"routed_to": message.recipient, "handoff_result": self._result_summary(result)},
            message_type="communication_handoff",
            task_id=task.task_id,
            channel=self.config.channel,
            context={"source_message": message.to_dict()},
            metadata={"coo": self.config.name, **self.config.metadata},
        )
        self.memory.add(response)
        return response, result

    @staticmethod
    def _result_summary(result: Any) -> Any:
        if isinstance(result, Deliverable):
            return {"department": result.department, "status": result.status, "artifact_type": result.artifact_type}
        return result


class COODepartment(Department):
    """Chief Operating Officer using an internal nanobot-style agent kernel."""

    name = "coo"
    allowed_tools = ["handoff", "route_department"]

    def __init__(
        self,
        *args,
        agent: Optional[COOAgentKernel] = None,
        config: Optional[COOAgentConfig] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.agent = agent or COOAgentKernel(config=config)
        self.agent.tools.register(
            COOTool(
                name="handoff",
                description="Submit a COO-routed task to the target department.",
                func=self._handoff,
            )
        )

    def get_context_requirements(self) -> list[str]:
        return ["project.name", "project.phase", "playbook.*"]

    async def process(self, task: CompanyTask) -> Deliverable:
        response, routed_deliverable = await self.agent.process_turn(task)
        source_message = response.context["source_message"]
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type=response.message_type,
            payload=response.to_dict(),
            status="success",
            metadata={
                "handoff_to": source_message["recipient"],
                "blocking": False,
                "routed_status": getattr(routed_deliverable, "status", None),
                "coo_memory": self.agent.memory.as_dicts(limit=5),
                "context": dict(task.context or {}),
            },
        )

    async def _handoff(self, *, task: CompanyTask, message: COOMessage) -> Optional[Deliverable]:
        if self._submit_task is None:
            return None
        payload = message.content if isinstance(message.content, dict) else {"message": message.content}
        handoff = CompanyTask(
            task_id=f"{task.task_id}-{message.recipient}",
            origin=self.name,
            target=message.recipient,
            artifact_type=str(payload.get("artifact_type") or task.artifact_type),
            payload=payload.get("payload", payload),
            blocking=bool(payload.get("blocking", False)),
            context={**dict(task.context or {}), "coo_message": message.to_dict()},
        )
        return await self._submit_task(handoff)
