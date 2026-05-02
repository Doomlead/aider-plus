from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class ProjectMemory:
    """Repo-scoped persistent state for agent context enrichment."""

    def __init__(self, repo_path: str):
        root = Path(repo_path).resolve()
        self.repo_path = str(root)
        self._memory_path = root / ".aider" / "project_memory.json"
        self._data: Dict[str, Any] = {}

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    def update(self, payload: Dict[str, Any]):
        self._data.update(payload)

    def load(self) -> Dict[str, Any]:
        if self._memory_path.exists():
            self._data = json.loads(self._memory_path.read_text(encoding="utf-8"))
        return self._data

    def persist(self):
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
