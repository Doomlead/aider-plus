from __future__ import annotations

import asyncio

from aider.company.context import ContextBuilder
from aider.company.department import Department
from aider.company.interfaces import Deliverable
from aider.company.recall import RecallEngine, RecallPacket
from aider.company.schemas import CompanyTask
from aider.company.state import CompanyStateManager
from aider.memory import MemoryRecord, MemoryStore, ProjectMemory


class NoopDepartment(Department):
    name = "engineering"

    async def process(self, task: CompanyTask) -> Deliverable:
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="memo",
            payload="unchanged",
            status="success",
            metadata={"context": task.context},
        )


def _task() -> CompanyTask:
    return CompanyTask(
        task_id="task-recall",
        origin="ceo",
        target="engineering",
        artifact_type="raw_prompt",
        payload={"original_request": "Build checkout retry telemetry for mobile users"},
        context={"thread_id": "thread-1", "channel_id": "eng", "user_id": "alice"},
    )


def test_recall_packet_scopes_department_private_and_channel_memory(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AIDER_MEMORY_CANONICAL_READER_FALLBACK", "1")
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    department = store.append_record(
        MemoryRecord(
            content="Engineering private note: checkout retry telemetry needs idempotency.",
            scope="department:engineering",
            visibility="private",
        )
    )
    channel = store.append_record(
        MemoryRecord(
            content="Channel note: mobile checkout retries should emit retry_count.",
            scope="channel:eng",
            visibility="project",
        )
    )
    pair_channel = store.append_record(
        MemoryRecord(
            content="Engineering and QA agreed to preserve checkout retry handoff notes.",
            scope="channel_pair:engineering:qa",
            visibility="project",
        )
    )
    product = store.append_record(
        MemoryRecord(
            content="Product-only launch note should not leak to engineering.",
            scope="department:product",
            visibility="private",
        )
    )

    packet = RecallEngine(store).build_recall_packet(_task())

    assert isinstance(packet, RecallPacket)
    assert [item["id"] for item in packet.department_private] == [department.id, pair_channel.id]
    assert [item["id"] for item in packet.channel] == [channel.id]
    assert product.id not in packet.why_included


def test_context_builder_injects_recall_packet_and_filters_visibility(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    store = MemoryStore(memory)
    visible = store.append_record(
        MemoryRecord(
            content="Project checkout telemetry decision is visible to departments.",
            scope="project",
            visibility="project",
        )
    )
    hidden = store.append_record(
        MemoryRecord(
            content="Private Product discovery must stay hidden.",
            scope="department:product",
            visibility="private",
        )
    )
    builder = ContextBuilder(CompanyStateManager(memory))

    context = builder.build(_task(), requirements=[])

    packet = context["recall_packet"]
    assert [item["id"] for item in packet["project"]] == [visible.id]
    all_ids = {
        item["id"]
        for section in (
            "thread",
            "department_private",
            "channel",
            "project",
            "user",
            "skills",
        )
        for item in packet[section]
    }
    assert hidden.id not in all_ids


def test_recall_packet_generates_explanations(tmp_path):
    store = MemoryStore(ProjectMemory(str(tmp_path)))
    record = store.append_record(
        MemoryRecord(
            content="Thread memory says checkout telemetry should include retry_count.",
            scope="thread:thread-1",
            visibility="project",
        )
    )

    packet = RecallEngine(store).build_recall_packet(_task()).to_dict()

    assert packet["thread"][0]["why_included"].startswith("Included from thread scope")
    assert "checkout" in packet["thread"][0]["why_included"]
    assert packet["why_included"][record.id] == packet["thread"][0]["why_included"]


def test_department_recall_helpers_do_not_change_process_result(tmp_path):
    department = NoopDepartment(ProjectMemory(str(tmp_path)))
    task = _task()
    task.context["recall_packet"] = {
        "thread": [
            {
                "id": "mem-1",
                "content": "Remember checkout telemetry constraints.",
                "why_included": "Included from thread scope because it matched task keyword(s): checkout.",
            }
        ],
        "department_private": [],
        "channel": [],
        "project": [],
        "user": [],
        "skills": [],
        "why_included": {"mem-1": "Included from thread scope."},
    }

    deliverable = asyncio.run(department.process(task))

    assert deliverable.payload == "unchanged"
    assert "Remember checkout telemetry" in department._format_recall_packet(task)
