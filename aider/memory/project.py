from __future__ import annotations

import json
from pathlib import Path
from copy import deepcopy
from typing import Any, Dict


class ProjectMemory:
    """Repo-scoped persistent state for agent context enrichment."""

    DEFAULTS: Dict[str, Any] = {
        "audit_log": [],
        "playbook": {
            "coding_standards": [],
            "ux_preferences": [],
            "deployment_gotchas": [],
        },
    }

    def __init__(self, repo_path: str):
        root = Path(repo_path).resolve()
        self.repo_path = str(root)
        self._memory_path = root / ".aider" / "project_memory.json"
        self._data: Dict[str, Any] = deepcopy(self.DEFAULTS)

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    def update(self, payload: Dict[str, Any]):
        self._data.update(payload)
        self._ensure_schema()

    def load(self) -> Dict[str, Any]:
        if self._memory_path.exists():
            self._data = json.loads(self._memory_path.read_text(encoding="utf-8"))
        self._ensure_schema()
        return self._data

    def persist(self):
        self._ensure_schema()
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _ensure_schema(self) -> None:
        for key, value in self.DEFAULTS.items():
            if key not in self._data:
                self._data[key] = deepcopy(value)
        playbook = self._data.get("playbook")
        if not isinstance(playbook, dict):
            playbook = {}
            self._data["playbook"] = playbook
        for key, value in self.DEFAULTS["playbook"].items():
            if not isinstance(playbook.get(key), list):
                playbook[key] = deepcopy(value)
        if not isinstance(self._data.get("audit_log"), list):
            self._data["audit_log"] = []
