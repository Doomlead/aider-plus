from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

from aider.company.audit import append_audit_event
from aider.company.project import Project
from aider.memory import ProjectMemory


class CompanyStateManager:
    """Single owner for company workflow state persisted in ProjectMemory."""

    def __init__(self, project_memory: ProjectMemory):
        self._memory = project_memory
        self.active_project: Optional[Project] = None

    @property
    def memory(self) -> ProjectMemory:
        return self._memory

    def get_project_id(self) -> str:
        if self.active_project:
            return self.active_project.project_id
        return str(
            self._memory.data.get("project_id")
            or getattr(self._memory, "repo_path", "")
        )

    def get_current_phase(self) -> Optional[str]:
        return self.active_project.phase if self.active_project else None

    def set_current_phase(self, phase: str) -> None:
        if self.active_project:
            self.active_project.phase = phase
        self._memory.update({"current_project_phase": phase})
        self._memory.persist()

    def add_pending_approval(self, approval: dict) -> None:
        approvals = [
            item
            for item in self.get_pending_approvals()
            if item.get("task_id") != approval.get("task_id")
        ]
        approvals.append(approval)
        self._memory.update({"pending_approvals": approvals})
        self._memory.persist()

    def remove_pending_approval(self, task_id: str) -> None:
        approvals = [
            item
            for item in self.get_pending_approvals()
            if item.get("task_id") != task_id
        ]
        self._memory.update({"pending_approvals": approvals})
        self._memory.persist()

    def get_pending_approvals(self) -> List[dict]:
        approvals = self._memory.data.get("pending_approvals", [])
        if not isinstance(approvals, list):
            return []
        return [item for item in approvals if isinstance(item, dict)]

    def get_playbook(self) -> dict:
        playbook = self._memory.data.get("playbook", {})
        return playbook if isinstance(playbook, dict) else {}

    def save_playbook(self, playbook: dict) -> None:
        self._memory.update({"playbook": playbook})
        self._memory.persist()

    def get_audit_log(self) -> List[dict]:
        records = self._memory.data.get("audit_log", [])
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]

    def append_audit_event(
        self,
        *,
        department: str,
        event_type: str,
        payload: Any,
        metadata: Optional[dict] = None,
    ) -> None:
        append_audit_event(
            self._memory,
            project_id=self.get_project_id(),
            department=department,
            event_type=event_type,
            payload=payload,
            metadata=metadata,
        )

    @staticmethod
    def pending_approval_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @classmethod
    def json_safe(cls, value: Any) -> Any:
        if is_dataclass(value):
            return cls.json_safe(asdict(value))
        if isinstance(value, dict):
            return {str(k): cls.json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls.json_safe(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
