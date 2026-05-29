import asyncio

from aider.company.events import (
    CompanyEvent,
    CooActionTaken,
    DaemonRunProgress,
    EventBus,
    LifecycleEvent,
    event_from_legacy_message,
)
from aider.company.schemas import CompanyEvent as LegacyCompanyEvent, EventMessage
from aider.company.surface_messages import (
    event_stream_response,
    format_daemon_progress,
    format_deployment_event,
    format_runtime_event_message,
    format_status_badge,
)


def test_event_bus_publish_subscribe_and_recent_replay():
    bus = EventBus(history_limit=2, session_id="test")
    seen = []

    unsubscribe = bus.subscribe(seen.append)
    first = bus.publish(LifecycleEvent(session_id="s1", payload={"task_id": "T1"}))
    second = bus.publish(
        DaemonRunProgress(session_id="s1", payload={"issue_id": "AP-1"})
    )
    third = bus.publish(CooActionTaken(session_id="s1", payload={"action": "route"}))
    unsubscribe()
    bus.publish(LifecycleEvent(session_id="s1", payload={"task_id": "T2"}))

    assert seen == [first, second, third]
    assert [event.event_type for event in bus.get_recent(limit=10)] == [
        "coo_action_taken",
        "lifecycle",
    ]
    assert bus.pruned_count == 2

    bus.set_history_limit(1)
    assert [event.event_type for event in bus.get_recent(limit=10)] == ["lifecycle"]
    assert bus.pruned_count == 3


def test_event_bus_publish_async_awaits_coroutine_handlers():
    bus = EventBus()
    seen = []

    async def handler(event):
        await asyncio.sleep(0)
        seen.append(event.event_type)

    bus.subscribe(handler)
    asyncio.run(bus.publish_async(DaemonRunProgress(payload={"issue_id": "AP-2"})))

    assert seen == ["daemon_run_progress"]


def test_legacy_event_message_converts_to_typed_runtime_event_and_sse():
    legacy = EventMessage(
        event=LegacyCompanyEvent.LIFECYCLE,
        task_id="AP-3",
        payload={"name": "daemon_run_progress", "stage": "qa", "status": "running"},
        metadata={"department": "daemon"},
    )

    event = event_from_legacy_message(legacy, session_id="daemon:test")
    message = format_runtime_event_message(event)
    bus = EventBus()
    bus.publish(event)

    assert isinstance(event, DaemonRunProgress)
    assert event.event_type == "daemon_run_progress"
    assert event.payload["task_id"] == "AP-3"
    assert event.severity == "info"
    assert "Stage: `qa`" in message
    assert "event: daemon_run_progress" in event_stream_response(bus)


def test_event_version_and_severity_defaults_are_stable():
    event = CompanyEvent.from_dict(
        {
            "event_type": "lifecycle",
            "session_id": "s1",
            "payload": {"task_id": "T1"},
            "severity": "warning",
            "version": 1,
        }
    )

    assert event.version == 1
    assert event.severity == "warning"
    assert event.is_deprecated is False
    assert event.deprecation_message is None
    assert event.to_dict()["severity"] == "warning"


def test_legacy_conversion_infers_warning_and_error_severity():
    warning_event = event_from_legacy_message(
        EventMessage(
            event=LegacyCompanyEvent.LIFECYCLE,
            task_id="AP-4",
            payload={"name": "daemon_run_progress", "status": "partial_success"},
        )
    )
    error_event = event_from_legacy_message(
        EventMessage(
            event=LegacyCompanyEvent.LIFECYCLE,
            task_id="AP-5",
            payload={"name": "daemon_run_progress", "status": "failed"},
        )
    )

    assert warning_event.severity == "warning"
    assert error_event.severity == "error"
    assert "Severity: `error`" in format_runtime_event_message(error_event)


def test_gui_desktop_and_discord_receive_same_runtime_events():
    from aider.company.events import DeploymentCompleted
    from aider.integrations.discord import subscribe_discord_event_forwarder

    bus = EventBus(session_id="surface-test")
    gui_events = []
    desktop_events = []
    discord_messages = []

    async def record_gui(event):
        gui_events.append(event)

    async def record_desktop(event):
        desktop_events.append(event)

    async def forward_discord(message):
        discord_messages.append(message)

    bus.subscribe(record_gui)
    bus.subscribe(record_desktop)
    subscribe_discord_event_forwarder(
        bus,
        forward_discord,
        event_types={"daemon_run_progress", "deployment_completed"},
    )

    progress = bus.publish(
        DaemonRunProgress(
            session_id="surface-test",
            payload={"issue_id": "AP-9", "stage": "qa", "status": "running"},
        )
    )
    deployment = bus.publish(
        DeploymentCompleted(
            session_id="surface-test",
            payload={"issue_id": "AP-9", "environment": "staging", "status": "success"},
        )
    )

    assert gui_events == [progress, deployment]
    assert desktop_events == [progress, deployment]
    assert [event.event_type for event in gui_events] == [
        event.event_type for event in desktop_events
    ]
    assert len(discord_messages) == 2
    assert "Daemon Run Progress" in discord_messages[0]
    assert "Deployment Completed" in discord_messages[1]
    assert "AP-9" in discord_messages[0]


def test_event_bus_get_recent_events_filters_and_replays_since_timestamp():
    bus = EventBus(history_limit=5, session_id="replay")
    first = bus.publish(
        LifecycleEvent(
            timestamp="2026-05-17T00:00:00+00:00",
            payload={"task_id": "T-1", "status": "running"},
        )
    )
    second = bus.publish(
        DaemonRunProgress(
            timestamp="2026-05-17T00:01:00+00:00",
            payload={"issue_id": "AP-10", "stage": "qa", "status": "running"},
        )
    )
    third = bus.publish(
        CooActionTaken(
            timestamp="2026-05-17T00:02:00+00:00",
            payload={"action": "route"},
        )
    )

    assert bus.get_recent_events(filter_by_type="daemon_run_progress") == [second]
    assert bus.get_recent_events(
        filter_by_type={"daemon_run_progress", "coo_action_taken"}, limit=5
    ) == [second, third]

    replayed = []
    count = bus.replay_to_subscriber(
        replayed.append, since_timestamp="2026-05-17T00:01:00+00:00"
    )

    assert count == 2
    assert replayed == [second, third]
    assert first not in replayed


def test_rich_formatters_support_success_warning_error_and_compact_modes():
    progress = DaemonRunProgress(
        payload={
            "issue_id": "AP-11",
            "stage": "devops",
            "status": "partial_success",
            "completed_count": 3,
            "total_stages": 5,
            "failed_stages": ["qa"],
        },
        severity="warning",
    )
    deployment = CompanyEvent.from_dict(
        {
            "event_type": "deployment_completed",
            "payload": {
                "issue_id": "AP-11",
                "environment": "staging",
                "status": "success",
                "deploy_url": "https://example.test",
            },
        }
    )
    failure = DaemonRunProgress(
        payload={
            "issue_id": "AP-12",
            "stage": "qa",
            "status": "failed",
            "error": "boom",
        },
        severity="error",
    )

    assert "⚠️" in format_daemon_progress(progress)
    assert "Progress: 3/5" in format_daemon_progress(progress)
    assert "Status: ⚠️ Partial Success" in format_daemon_progress(progress)
    assert "✅" in format_deployment_event(deployment)
    assert "https://example.test" in format_deployment_event(deployment)
    assert format_status_badge("running") == "🏃 Running"
    assert "❌" in format_runtime_event_message(failure, compact=True)
    assert "AP-12" in format_runtime_event_message(failure, compact=True)
    assert "\033[" in format_runtime_event_message(failure, ansi=True)
