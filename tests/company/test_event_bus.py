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
    format_runtime_event_message,
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
