"""Nanobot-backed communication primitives for Company Mode."""

from __future__ import annotations

import asyncio
import json
import shutil
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable


@dataclass
class NanobotConfig:
    """Optional bridge settings for using nanobot as the communication layer."""

    enabled: bool = False
    coo_department: str = "coo"
    gateway_url: Optional[str] = None
    channel: str = "company"
    timeout_seconds: float = 3.0
    metadata: dict[str, Any] = field(default_factory=dict)


class NanobotBridge:
    """Small adapter that emits Company messages in a nanobot-friendly shape.

    The bridge is intentionally optional. When a nanobot gateway URL is not
    configured, it still records normalized communication packets so internal
    orchestration, audits, and tests use the same contract that an external
    nanobot channel would receive.
    """

    def __init__(self, config: Optional[NanobotConfig] = None):
        self.config = config or NanobotConfig()
        self.messages: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    @property
    def nanobot_cli_available(self) -> bool:
        return shutil.which("nanobot") is not None

    async def publish(
        self,
        *,
        sender: str,
        recipient: str,
        message_type: str,
        payload: Any,
        task_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        packet = {
            "channel": self.config.channel,
            "sender": sender,
            "recipient": recipient,
            "type": message_type,
            "task_id": task_id,
            "payload": payload,
            "metadata": {**self.config.metadata, **dict(metadata or {})},
        }
        self.messages.append(packet)
        if self.enabled and self.config.gateway_url:
            await asyncio.to_thread(self._post_gateway, packet)
        return packet

    def _post_gateway(self, packet: dict[str, Any]) -> None:
        data = json.dumps(packet).encode("utf-8")
        request = urllib.request.Request(
            self.config.gateway_url or "",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds):
            return


class COODepartment(Department):
    """Chief Operating Officer department for Nanobot-style communication.

    The COO is the communication hub: it normalizes incoming user/company
    messages, publishes a nanobot-compatible packet, then hands work to the
    requested department when the payload names a target department.
    """

    name = "coo"
    allowed_tools = ["nanobot_channel", "handoff"]

    def __init__(self, *args, bridge: Optional[NanobotBridge] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.bridge = bridge or NanobotBridge()

    def get_context_requirements(self) -> list[str]:
        return ["project.name", "project.phase", "playbook.*"]

    async def process(self, task: CompanyTask) -> Deliverable:
        payload = task.payload if isinstance(task.payload, dict) else {"message": task.payload}
        target = str(payload.get("target_department") or payload.get("target") or "product")
        packet = await self.bridge.publish(
            sender=task.origin,
            recipient=target,
            message_type=str(payload.get("message_type") or task.artifact_type),
            payload=payload,
            task_id=task.task_id,
            metadata={"coo": self.name, "nanobot_cli_available": self.bridge.nanobot_cli_available},
        )

        routed_deliverable = None
        if target != self.name and self._submit_task is not None:
            handoff = CompanyTask(
                task_id=f"{task.task_id}-{target}",
                origin=self.name,
                target=target,
                artifact_type=str(payload.get("artifact_type") or task.artifact_type),
                payload=payload.get("payload", payload),
                blocking=bool(payload.get("blocking", False)),
                context={**dict(task.context or {}), "nanobot_packet": packet},
            )
            routed_deliverable = await self._submit_task(handoff)

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="communication_handoff",
            payload={"packet": packet, "routed_to": target},
            status="success",
            metadata={
                "handoff_to": target,
                "blocking": False,
                "routed_status": getattr(routed_deliverable, "status", None),
                "context": dict(task.context or {}),
            },
        )
