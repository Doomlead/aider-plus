import asyncio

from aider.company.events import (
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
    assert "Stage: `qa`" in message
    assert "event: daemon_run_progress" in event_stream_response(bus)
