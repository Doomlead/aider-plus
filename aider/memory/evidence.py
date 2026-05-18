from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from aider.company.project import Project
from aider.memory.records import MemoryRecord
from aider.memory.store import MemoryStore


@dataclass
class SkillEvidenceCluster:
    """Related memory records that suggest a repeatable procedure."""

    cluster_id: str
    department: str
    channel: str
    thread_id: str
    outcome: str
    records: list[MemoryRecord] = field(default_factory=list)
    procedure_steps: list[str] = field(default_factory=list)
    outcome_summary: str = ""
    suggested_scope: str = "shared"

    @property
    def source_memory_records(self) -> list[str]:
        return [record.id for record in self.records]

    @property
    def source_tasks(self) -> list[str]:
        task_ids = set()
        for record in self.records:
            evidence = (
                record.skill_evidence if isinstance(record.skill_evidence, dict) else {}
            )
            task_id = record.metadata.get("task_id") or evidence.get("task_id")
            if task_id:
                task_ids.add(str(task_id))
        return sorted(task_ids)

    @property
    def confidence(self) -> float:
        successful = self.outcome == "success"
        base = 0.55 if successful else 0.4
        return min(
            0.95, base + (0.08 * len(self.records)) + (0.04 * len(self.procedure_steps))
        )


def collect_evidence_for_project(
    project: Project,
    store: MemoryStore,
    *,
    min_records: int = 2,
) -> list[SkillEvidenceCluster]:
    """Cluster project memory by department/channel/thread/outcome.

    The memory fabric records department communications, deliverables, and explicit
    ``skill_evidence`` blocks with consistent metadata. This helper keeps the
    clustering intentionally deterministic and conservative: only clusters with
    repeated successful evidence are returned by default.
    """

    records = _candidate_records(store.query_records())
    buckets: dict[tuple[str, str, str, str], list[MemoryRecord]] = defaultdict(list)
    for record in records:
        department = _department(record)
        channel = _channel(record)
        thread_id = _thread_id(record, project)
        outcome = _outcome(record)
        buckets[(department, channel, thread_id, outcome)].append(record)

    clusters: list[SkillEvidenceCluster] = []
    for idx, ((department, channel, thread_id, outcome), grouped) in enumerate(
        sorted(buckets.items(), key=lambda item: item[0])
    ):
        if outcome != "success" or len(grouped) < min_records:
            continue
        ordered = sorted(grouped, key=lambda record: record.created_at)
        steps = _procedure_steps(ordered, department)
        summary = _outcome_summary(ordered, outcome, department, channel)
        cluster_id = _cluster_id(project, department, channel, thread_id, outcome, idx)
        clusters.append(
            SkillEvidenceCluster(
                cluster_id=cluster_id,
                department=department,
                channel=channel,
                thread_id=thread_id,
                outcome=outcome,
                records=ordered,
                procedure_steps=steps,
                outcome_summary=summary,
                suggested_scope=_suggested_scope(department),
            )
        )
    return clusters


def _candidate_records(records: Iterable[MemoryRecord]) -> list[MemoryRecord]:
    candidates: list[MemoryRecord] = []
    for record in records:
        if record.skill_evidence is not None:
            candidates.append(record)
            continue
        if record.kind in {
            "deliverable_produced",
            "approval_decision",
            "route_decision",
        }:
            candidates.append(record)
    return candidates


def _department(record: MemoryRecord) -> str:
    evidence = record.skill_evidence if isinstance(record.skill_evidence, dict) else {}
    metadata = record.metadata or {}
    value = (
        evidence.get("role")
        or evidence.get("department")
        or metadata.get("department")
        or metadata.get("origin")
        or record.author
    )
    if not value and record.scope.startswith("department:"):
        value = record.scope.split(":", 1)[1]
    return str(value or "shared").lower().strip() or "shared"


def _channel(record: MemoryRecord) -> str:
    metadata = record.metadata or {}
    value = (
        metadata.get("channel")
        or metadata.get("surface")
        or metadata.get("artifact_type")
    )
    return str(value or record.kind or "memory").lower().strip() or "memory"


def _thread_id(record: MemoryRecord, project: Project) -> str:
    metadata = record.metadata or {}
    value = metadata.get("thread_id") or metadata.get("session_id")
    return str(value or project.project_id or "project").lower().strip() or "project"


def _outcome(record: MemoryRecord) -> str:
    evidence = record.skill_evidence if isinstance(record.skill_evidence, dict) else {}
    metadata = record.metadata or {}
    value = evidence.get("outcome") or metadata.get("status")
    if value is None and record.kind == "approval_decision":
        value = "success" if metadata.get("approved") else "failure"
    normalized = str(value or "unknown").lower().strip()
    if normalized in {
        "ok",
        "passed",
        "pass",
        "approved",
        "done",
        "complete",
        "completed",
    }:
        return "success"
    if normalized in {"failed", "rejected", "error"}:
        return "failure"
    return normalized or "unknown"


def _procedure_steps(records: list[MemoryRecord], department: str) -> list[str]:
    steps: list[str] = []
    for record in records:
        step = _step_from_record(record, department)
        if step and step not in steps:
            steps.append(step)
        if len(steps) >= 6:
            break
    if not steps:
        steps.append(
            f"Review prior {department} evidence before starting similar work."
        )
    return steps


def _step_from_record(record: MemoryRecord, department: str) -> str:
    metadata = record.metadata or {}
    evidence = record.skill_evidence if isinstance(record.skill_evidence, dict) else {}
    event_type = str(
        metadata.get("event_type") or record.kind or "memory event"
    ).replace("_", " ")
    content = str(record.content or "").replace("\n", " ").strip()
    signals = (
        evidence.get("signals") if isinstance(evidence.get("signals"), dict) else {}
    )
    if signals:
        signal_text = ", ".join(
            f"{key}: {value}" for key, value in list(signals.items())[:2]
        )
        return f"Use the observed {event_type} signals ({signal_text}) when the task matches."
    if content:
        return f"Apply the {department} {event_type} pattern: {content[:160]}"
    return f"Repeat the successful {department} {event_type} pattern."


def _outcome_summary(
    records: list[MemoryRecord], outcome: str, department: str, channel: str
) -> str:
    tasks = sorted(
        {
            str(record.metadata.get("task_id"))
            for record in records
            if record.metadata.get("task_id")
        }
    )
    task_text = f" across tasks {', '.join(tasks[:5])}" if tasks else ""
    return (
        f"{len(records)} {outcome} {department} memory records on {channel}{task_text}."
    )


def _suggested_scope(department: str) -> str:
    known = {
        "coo",
        "product",
        "ux",
        "engineering",
        "reviewer",
        "qa",
        "delivery",
        "devops",
    }
    return department if department in known else "shared"


def _cluster_id(
    project: Project,
    department: str,
    channel: str,
    thread_id: str,
    outcome: str,
    idx: int,
) -> str:
    safe = "-".join(
        part.replace("/", "-").replace(" ", "-")
        for part in (
            project.project_id,
            department,
            channel,
            thread_id,
            outcome,
            str(idx),
        )
    )
    return safe[:160]
