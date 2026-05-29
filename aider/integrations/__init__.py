"""Integration helpers for external front-ends (eg: Discord or Slack bots)."""

from .adapters import AdapterMessage, ThinAdapter
from .discord import (
    DiscordAiderBot,
    DiscordAiderConfig,
    DiscordSessionKey,
    DiscordSessionManager,
    RepositoryPolicy,
)
from .matrix import MatrixAdapter, MatrixAdapterConfig
from .slack import (
    MattermostAdapter,
    SlackAdapter,
    SlackAdapterConfig,
    TeamsAdapter,
    WebhookAdapter,
)

__all__ = [
    "AdapterMessage",
    "ThinAdapter",
    "DiscordAiderBot",
    "DiscordAiderConfig",
    "DiscordSessionKey",
    "DiscordSessionManager",
    "RepositoryPolicy",
    "SlackAdapter",
    "SlackAdapterConfig",
    "TeamsAdapter",
    "MattermostAdapter",
    "MatrixAdapter",
    "MatrixAdapterConfig",
    "WebhookAdapter",
]
