import asyncio

from aider.company.events import (
    CooActionTaken,
    DaemonRunProgress,
    EventBus,
    LifecycleEvent,
)
from aider.integrations.adapters import AdapterMessage, ThinAdapter
from aider.integrations.discord import (
    DiscordAiderBot,
    subscribe_discord_event_forwarder,
)
from aider.integrations.slack import (
    MattermostAdapter,
    SlackAdapter,
    TeamsAdapter,
    WebhookAdapter,
)


def test_thin_adapter_normalizes_mapping_and_delegates_input():
    handled = []

    def input_handler(message: AdapterMessage):
        handled.append(message)
        return {"status": "ok", "session_id": message.session_id}

    adapter = ThinAdapter(input_handler=input_handler)

    result = asyncio.run(
        adapter.handle_user_input(
            {
                "text": "  build the feature  ",
                "user": "U1",
                "channel": "C1",
                "repo_path": "/repo",
                "team": "T1",
            }
        )
    )

    assert result == {"status": "ok", "session_id": "adapter:C1:/repo"}
    assert handled[0].text == "build the feature"
    assert handled[0].user_id == "U1"
    assert handled[0].channel_id == "C1"
    assert handled[0].metadata["team"] == "T1"


def test_thin_adapter_subscribes_to_event_bus_and_formats_selected_events():
    bus = EventBus(session_id="adapter-test")
    forwarded = []
    adapter = ThinAdapter(event_bus=bus, forward=forwarded.append)
    adapter.subscribe_to_bus(event_types={"daemon_run_progress"})

    ignored = bus.publish(LifecycleEvent(payload={"task_id": "T1"}))
    progress = bus.publish(
        DaemonRunProgress(payload={"issue_id": "AP-22", "stage": "qa"})
    )

    assert ignored.event_type == "lifecycle"
    assert progress.event_type == "daemon_run_progress"
    assert len(forwarded) == 1
    assert "Daemon Run Progress" in forwarded[0]
    assert "AP-22" in forwarded[0]


def test_discord_adapter_inherits_thin_adapter_and_reuses_event_forwarding():
    bus = EventBus(session_id="discord-test")
    messages = []
    bot = DiscordAiderBot(event_bus=bus, forward=messages.append)

    normalized = bot.normalize_message(
        " hello ", user_id=7, channel_id=9, repo_path="/repo"
    )
    bot.subscribe_to_bus(event_types={"coo_action_taken"})
    bus.publish(CooActionTaken(payload={"action": "route", "task_id": "T-7"}))

    assert isinstance(bot, ThinAdapter)
    assert normalized.surface == "discord"
    assert normalized.session_id == "discord:9:/repo"
    assert "Coo Action Taken" in messages[0]


def test_legacy_discord_forwarder_uses_thin_adapter_subscription():
    bus = EventBus(session_id="discord-forwarder")
    messages = []

    subscribe_discord_event_forwarder(
        bus, messages.append, event_types={"daemon_run_progress"}
    )
    bus.publish(DaemonRunProgress(payload={"issue_id": "AP-23", "stage": "dev"}))

    assert len(messages) == 1
    assert messages[0].startswith(">>>")
    assert "AP-23" in messages[0]


def test_slack_and_webhook_adapter_normalize_slack_events_and_forward_bus():
    bus = EventBus(session_id="slack-test")
    messages = []
    adapter = SlackAdapter(event_bus=bus, forward=messages.append)

    normalized = asyncio.run(
        adapter.handle_user_input(
            {
                "team_id": "T1",
                "event": {
                    "text": "ship it",
                    "user": "U2",
                    "channel": "C2",
                    "thread_ts": "123.4",
                },
            }
        )
    )
    adapter.subscribe_to_bus(event_types={"daemon_run_progress"})
    bus.publish(DaemonRunProgress(payload={"issue_id": "AP-24", "status": "running"}))

    assert WebhookAdapter is SlackAdapter
    assert normalized.surface == "slack"
    assert normalized.text == "ship it"
    assert normalized.user_id == "U2"
    assert normalized.channel_id == "C2"
    assert normalized.thread_id == "123.4"
    assert normalized.metadata["team_id"] == "T1"
    assert "AP-24" in messages[0]


def test_cli_adapter_flag_accepts_repeatable_thin_adapter_choices():
    from aider.args import get_parser

    parser = get_parser([], None)
    args = parser.parse_args(["--adapter", "slack", "--adapter", "webhook", "--adapter", "teams", "--adapter", "mattermost"])

    assert args.adapter == ["slack", "webhook", "teams", "mattermost"]


def test_teams_and_mattermost_adapters_override_surface_name():
    assert TeamsAdapter().surface_name == "teams"
    assert MattermostAdapter().surface_name == "mattermost"
