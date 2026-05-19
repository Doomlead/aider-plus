from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from .repository import JsonMemoryRepository, MemoryRepository


class ProjectMemory:
    """Repo-scoped persistent state for agent context enrichment."""

    DEFAULTS: Dict[str, Any] = {
        "skill_proposals": [],
        "memory": {
            "records": [],
            "threads": [],
            "migration_log": [],
            "corrupt_backup": [],
            "policy": {
                "reinforcement_weight": 0.3,
                "recency_weight": 0.2,
                "min_usage_for_acceptance": 1,
            },
        },
        "observability": {
            "turns_per_phase": {},
            "token_usage_per_department": {},
            "qa_metrics": {
                "total_runs": 0,
                "passed": 0,
                "failed": 0,
                "no_tests": 0,
                "pass_rate": 0.0,
            },
            "task_metrics": {
                "total_tasks": 0,
                "qa_revision_cycles": 0,
                "engineering_revision_cycles": 0,
                "avg_qa_revisions": 0.0,
            },
        },
    }

    def __init__(self, repo_path: str, repository: Optional[MemoryRepository] = None):
        root = Path(repo_path).resolve()
        self.repo_path = str(root)
        self._memory_path = root / ".aider" / "project_memory.json"
        self.repository = repository or JsonMemoryRepository(
            self._memory_path, self.DEFAULTS
        )
        self._data: Dict[str, Any] = self.repository.migrate(deepcopy(self.DEFAULTS))

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    def update(self, payload: Dict[str, Any]):
        self._data.update(payload)
        self._ensure_schema()

    def load(self) -> Dict[str, Any]:
        self._data = self.repository.load()
        self._ensure_schema()
        return self._data

    def persist(self):
        self._ensure_schema()
        self.repository.save(self._data)
        self._data = self.repository.migrate(self._data)

    def _ensure_schema(self) -> None:
        self._data = self.repository.migrate(self._data)

    def memory_policy(self) -> Dict[str, Any]:
        memory = self._data.setdefault("memory", {})
        policy = memory.setdefault("policy", {})
        return dict(policy)
