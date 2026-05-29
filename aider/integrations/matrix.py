"""Matrix thin chat adapter for Aider Plus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aider.company.events import CompanyEvent, EventBus
from aider.company.surface_messages import format_runtime_event_message
from aider.integrations.adapters import (
    AdapterMessage,
    EventForwarder,
    InputHandler,
    ThinAdapter,
)


@dataclass
class MatrixAdapterConfig:
    """Minimal Matrix adapter settings that avoid importing a Matrix SDK."""

    homeserver: str | None = None
    access_token: str | None = None
    user_id: str | None = None
    max_message_chars: int = 3000


class MatrixAdapter(ThinAdapter):
    """Thin Matrix-compatible chat adapter using normalized room events.

    Matrix SDKs or webhook bridges can pass raw room event dictionaries to
    ``handle_user_input``. This adapter only extracts Matrix identity fields;
    workflow execution and status updates stay in the shared runtime and
    EventBus formatting path.
    """

    surface_name = "matrix"

    def __init__(
        self,
        config: MatrixAdapterConfig | None = None,
        *,
        event_bus: EventBus | None = None,
        forward: EventForwarder | None = None,
        input_handler: InputHandler | None = None,
    ):
        super().__init__(
            event_bus=event_bus,
            forward=forward,
            input_handler=input_handler,
            event_formatter=self.format_event,
        )
        self.config = config or MatrixAdapterConfig()

    def normalize_message(self, raw: Any, **overrides: Any) -> AdapterMessage:
        payload: dict[str, Any]
        if isinstance(raw, Mapping):
            payload = dict(raw)
            content = payload.get("content")
            if isinstance(content, Mapping):
                payload = {**payload, **content}
        else:
            payload = {"body": raw}

        room_id = payload.get("room_id") or payload.get("room")
        event_id = payload.get("event_id") or payload.get("event")
        thread_id = payload.get("thread_id") or event_id
        text = payload.get("body") or payload.get("formatted_body") or payload.get("text")

        return super().normalize_message(
            payload,
            text=overrides.pop("text", text),
            user_id=overrides.pop("user_id", payload.get("sender")),
            channel_id=overrides.pop("channel_id", room_id),
            thread_id=overrides.pop("thread_id", thread_id),
            **overrides,
        )

    def format_event(self, event: CompanyEvent) -> str:
        return format_runtime_event_message(event)[: self.config.max_message_chars]
