"""Shared thin-adapter primitives for chat, webhook, and bot surfaces.

Adapters should normalize surface-specific messages into one small envelope,
forward user text into the shared Aider/Company runtime, and render EventBus
updates with the common surface message formatters. Product workflow,
approvals, audit semantics, and COO routing stay in the shared runtime.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional, Set

from aider.company.events import CompanyEvent, EventBus
from aider.company.surface_messages import format_runtime_event_message

HIGH_PRIORITY_EVENT_TYPES = {
    "approval_required",
    "project_blocked",
    "daemon_run_progress",
    "deployment_completed",
    "coo_action_taken",
}

EventFormatter = Callable[[CompanyEvent], str]
EventForwarder = Callable[[str], Awaitable[None] | None]
InputHandler = Callable[["AdapterMessage"], Awaitable[Any] | Any]


@dataclass(frozen=True)
class AdapterMessage:
    """Normalized inbound message shared by all thin adapters."""

    surface: str
    text: str
    user_id: Optional[str] = None
    channel_id: Optional[str] = None
    thread_id: Optional[str] = None
    session_id: Optional[str] = None
    repo_path: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ThinAdapter:
    """Base class for replaceable chat/webhook surfaces.

    Subclasses own transport-specific details such as Slack signing, Discord
    intents, Matrix rooms, or webhook auth. This base owns only normalization,
    EventBus forwarding, and delegation into the shared runtime.
    """

    surface_name = "adapter"
    default_event_types: Set[str] = HIGH_PRIORITY_EVENT_TYPES

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        forward: EventForwarder | None = None,
        input_handler: InputHandler | None = None,
        event_formatter: EventFormatter | None = None,
    ):
        self.event_bus = event_bus
        self.forward = forward
        self.input_handler = input_handler
        self.event_formatter = event_formatter or format_runtime_event_message
        self._unsubscribe: Callable[[], None] | None = None

    def normalize_message(self, raw: Any, **overrides: Any) -> AdapterMessage:
        """Normalize string/dict/object transport payloads into AdapterMessage."""

        values: dict[str, Any] = {}
        if isinstance(raw, AdapterMessage):
            values.update(raw.__dict__)
        elif isinstance(raw, str):
            values["text"] = raw
        elif isinstance(raw, Mapping):
            values.update(raw)
        else:
            for attr in (
                "text",
                "content",
                "user_id",
                "channel_id",
                "thread_id",
                "session_id",
                "repo_path",
            ):
                if hasattr(raw, attr):
                    values[attr] = getattr(raw, attr)

        values.update(
            {key: value for key, value in overrides.items() if value is not None}
        )
        text = (
            values.get("text") or values.get("content") or values.get("message") or ""
        )
        user_id = values.get("user_id") or values.get("user") or values.get("author_id")
        channel_id = (
            values.get("channel_id") or values.get("channel") or values.get("room_id")
        )
        thread_id = (
            values.get("thread_id") or values.get("thread_ts") or values.get("ts")
        )
        session_id = values.get("session_id") or self.session_id_for(
            channel_id=channel_id,
            thread_id=thread_id,
            user_id=user_id,
            repo_path=values.get("repo_path"),
        )
        metadata = dict(values.get("metadata") or {})
        for key, value in values.items():
            if key not in {
                "surface",
                "text",
                "content",
                "message",
                "user_id",
                "user",
                "author_id",
                "channel_id",
                "channel",
                "room_id",
                "thread_id",
                "thread_ts",
                "ts",
                "session_id",
                "repo_path",
                "metadata",
            }:
                metadata[key] = value

        return AdapterMessage(
            surface=str(values.get("surface") or self.surface_name),
            text=str(text).strip(),
            user_id=None if user_id is None else str(user_id),
            channel_id=None if channel_id is None else str(channel_id),
            thread_id=None if thread_id is None else str(thread_id),
            session_id=None if session_id is None else str(session_id),
            repo_path=values.get("repo_path"),
            metadata=metadata,
        )

    def session_id_for(
        self,
        *,
        channel_id: Any = None,
        thread_id: Any = None,
        user_id: Any = None,
        repo_path: Any = None,
    ) -> str:
        """Build a stable runtime session id from normalized surface identity."""

        subject = thread_id or channel_id or user_id or "default"
        repo_suffix = f":{repo_path}" if repo_path else ""
        return f"{self.surface_name}:{subject}{repo_suffix}"

    async def send_event(self, event: CompanyEvent) -> str:
        """Render and optionally forward one EventBus event."""

        message = self.event_formatter(event)
        if self.forward:
            result = self.forward(message)
            if asyncio.iscoroutine(result):
                await result
        return message

    def subscribe_to_bus(
        self,
        event_bus: EventBus | None = None,
        *,
        event_types: Set[str] | None = None,
        replay: bool = False,
    ) -> Callable[[], None]:
        """Subscribe this adapter to selected EventBus event types."""

        bus = event_bus or self.event_bus
        if bus is None:
            raise ValueError("event_bus is required to subscribe a thin adapter")
        selected = set(event_types or self.default_event_types)

        async def handler(event: CompanyEvent):
            if event.event_type in selected:
                await self.send_event(event)

        self._unsubscribe = bus.subscribe(handler, replay=replay)
        return self._unsubscribe

    async def handle_user_input(self, raw: Any, **overrides: Any) -> Any:
        """Normalize inbound user input and delegate it to the shared runtime."""

        message = self.normalize_message(raw, **overrides)
        if not self.input_handler:
            return message
        result = self.input_handler(message)
        if asyncio.iscoroutine(result):
            return await result
        return result
