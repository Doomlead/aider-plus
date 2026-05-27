"""Slack/Webhook thin adapter foundation for Aider Plus."""

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
class SlackAdapterConfig:
    """Minimal Slack adapter settings that avoid importing a Slack SDK."""

    signing_secret: str | None = None
    bot_token: str | None = None
    default_channel: str | None = None
    max_message_chars: int = 3000


class SlackAdapter(ThinAdapter):
    """Small Slack-compatible adapter using normalized event dictionaries.

    The class intentionally does not depend on a Slack SDK. HTTP frameworks or
    Bolt apps can pass Slack event payload dictionaries to ``handle_user_input``
    and provide a ``forward`` callable that posts rendered EventBus messages.
    """

    surface_name = "slack"

    def __init__(
        self,
        config: SlackAdapterConfig | None = None,
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
        self.config = config or SlackAdapterConfig()

    def normalize_message(self, raw: Any, **overrides: Any) -> AdapterMessage:
        payload: dict[str, Any]
        if (
            isinstance(raw, Mapping)
            and "event" in raw
            and isinstance(raw["event"], Mapping)
        ):
            payload = {**raw, **raw["event"]}
        elif isinstance(raw, Mapping):
            payload = dict(raw)
        else:
            payload = {"text": raw}

        return super().normalize_message(
            payload,
            user_id=overrides.pop("user_id", payload.get("user")),
            channel_id=overrides.pop("channel_id", payload.get("channel")),
            thread_id=overrides.pop(
                "thread_id", payload.get("thread_ts") or payload.get("ts")
            ),
            **overrides,
        )

    def format_event(self, event: CompanyEvent) -> str:
        return format_runtime_event_message(event)[: self.config.max_message_chars]


class TeamsAdapter(SlackAdapter):
    """Thin Microsoft Teams-compatible adapter that reuses Slack payload shape."""

    surface_name = "teams"


class MattermostAdapter(SlackAdapter):
    """Thin Mattermost-compatible adapter that reuses Slack payload shape."""

    surface_name = "mattermost"


WebhookAdapter = SlackAdapter
