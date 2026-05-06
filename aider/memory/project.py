from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from .repository import JsonMemoryRepository, MemoryRepository


class ProjectMemory:
    """Repo-scoped persistent state for agent context enrichment."""

    DEFAULTS: Dict[str, Any] = {
        "audit_log": [],
        "playbook": {
            "coding_standards": [],
            "ux_preferences": [],
            "deployment_gotchas": [],
        },
        "observability": {
            "turns_per_phase": {},
            "token_usage_per_department": {},
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
