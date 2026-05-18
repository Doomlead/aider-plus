from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable

CURRENT_SCHEMA_VERSION = 4


class MemoryRepository(ABC):
    """Persistence boundary for project memory storage backends."""

    @abstractmethod
    def load(self) -> Dict[str, Any]:
        """Load the persisted project memory document."""

    @abstractmethod
    def save(self, data: Dict[str, Any]) -> None:
        """Persist the complete project memory document."""

    @abstractmethod
    def migrate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Return data upgraded to the current project memory schema."""


class ProjectMemoryMigrator:
    """Small forward-only migrator for project memory schema changes."""

    def __init__(self, defaults: Dict[str, Any]):
        self.defaults = defaults

    def migrate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        migrated = deepcopy(data) if isinstance(data, dict) else {}
        version = int(migrated.get("schema_version") or 1)

        if version < 2:
            migrated = self._migrate_to_v2(migrated)
            version = 2

        if version < 3:
            migrated = self._migrate_to_v3(migrated)
            version = 3

        if version < 4:
            migrated = self._migrate_to_v4(migrated)
            version = 4

        migrated["schema_version"] = CURRENT_SCHEMA_VERSION
        self._ensure_defaults(migrated)
        return migrated

    def _migrate_to_v2(self, data: Dict[str, Any]) -> Dict[str, Any]:
        observability = data.get("observability")
        if not isinstance(observability, dict):
            data["observability"] = {
                "turns_per_phase": {},
                "token_usage_per_department": {},
            }
        return data

    def _migrate_to_v3(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Expand token_usage_per_department from {dept: int} to {dept: UsageRecord}.
        Add qa_metrics and task_metrics to observability.
        """
        observability = data.get("observability")
        if not isinstance(observability, dict):
            observability = {}
            data["observability"] = observability

        # Migrate flat token counts to structured records.
        raw_usage = observability.get("token_usage_per_department", {})
        if isinstance(raw_usage, dict):
            structured: Dict[str, Any] = {}
            for dept, value in raw_usage.items():
                if isinstance(value, int):
                    # Old format: just a total.
                    structured[dept] = {
                        "total_tokens": value,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "estimated_cost_usd": 0.0,
                        "run_count": 0,
                    }
                elif isinstance(value, dict):
                    # Already structured — ensure all keys present.
                    structured[dept] = {
                        "total_tokens": int(value.get("total_tokens", 0) or 0),
                        "prompt_tokens": int(value.get("prompt_tokens", 0) or 0),
                        "completion_tokens": int(
                            value.get("completion_tokens", 0) or 0
                        ),
                        "estimated_cost_usd": float(
                            value.get("estimated_cost_usd", 0.0) or 0.0
                        ),
                        "run_count": int(value.get("run_count", 0) or 0),
                    }
            observability["token_usage_per_department"] = structured

        # Add qa_metrics if absent.
        observability.setdefault(
            "qa_metrics",
            {
                "total_runs": 0,
                "passed": 0,
                "failed": 0,
                "no_tests": 0,
                "pass_rate": 0.0,
            },
        )

        # Add task_metrics if absent.
        observability.setdefault(
            "task_metrics",
            {
                "total_tasks": 0,
                "qa_revision_cycles": 0,
                "engineering_revision_cycles": 0,
                "avg_qa_revisions": 0.0,
            },
        )

        return data

    def _migrate_to_v4(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Add the local-first memory namespace without touching legacy keys."""
        memory = data.get("memory")
        if not isinstance(memory, dict):
            memory = {}
            data["memory"] = memory
        memory.setdefault("records", [])
        memory.setdefault("threads", [])
        return data

    def _ensure_defaults(self, data: Dict[str, Any]) -> None:
        for key, value in self.defaults.items():
            if key not in data:
                data[key] = deepcopy(value)
        playbook = data.get("playbook")
        if not isinstance(playbook, dict):
            playbook = {}
            data["playbook"] = playbook
        for key, value in self.defaults["playbook"].items():
            if not isinstance(playbook.get(key), list):
                playbook[key] = deepcopy(value)
        if not isinstance(data.get("audit_log"), list):
            data["audit_log"] = []
        memory = data.get("memory")
        if not isinstance(memory, dict):
            memory = {}
            data["memory"] = memory
        if not isinstance(memory.get("records"), list):
            memory["records"] = []
        if not isinstance(memory.get("threads"), list):
            memory["threads"] = []
        observability = data.get("observability")
        if not isinstance(observability, dict):
            observability = {}
            data["observability"] = observability
        if not isinstance(observability.get("turns_per_phase"), dict):
            observability["turns_per_phase"] = {}
        if not isinstance(observability.get("token_usage_per_department"), dict):
            observability["token_usage_per_department"] = {}

        # v3 keys
        observability.setdefault(
            "qa_metrics",
            {
                "total_runs": 0,
                "passed": 0,
                "failed": 0,
                "no_tests": 0,
                "pass_rate": 0.0,
            },
        )
        observability.setdefault(
            "task_metrics",
            {
                "total_tasks": 0,
                "qa_revision_cycles": 0,
                "engineering_revision_cycles": 0,
                "avg_qa_revisions": 0.0,
            },
        )


class JsonMemoryRepository(MemoryRepository):
    """JSON-file implementation for repo-scoped project memory."""

    def __init__(self, memory_path: Path, defaults: Dict[str, Any]):
        self.memory_path = memory_path
        self.migrator = ProjectMemoryMigrator(defaults)

    def load(self) -> Dict[str, Any]:
        if not self.memory_path.exists():
            return self.migrate({})
        return self.migrate(json.loads(self.memory_path.read_text(encoding="utf-8")))

    def save(self, data: Dict[str, Any]) -> None:
        migrated = self.migrate(data)
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory_path.write_text(
            json.dumps(migrated, indent=2, sort_keys=True), encoding="utf-8"
        )

    def migrate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self.migrator.migrate(data)


class SQLiteMemoryRepository(MemoryRepository):
    """SQLite implementation storing the project memory document behind the same boundary."""

    def __init__(self, db_path: Path, defaults: Dict[str, Any], key: str = "default"):
        self.db_path = db_path
        self.key = key
        self.migrator = ProjectMemoryMigrator(defaults)

    def load(self) -> Dict[str, Any]:
        self._ensure_database()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT data FROM project_memory WHERE key = ?", (self.key,)
            ).fetchone()
        if row is None:
            return self.migrate({})
        return self.migrate(json.loads(row[0]))

    def save(self, data: Dict[str, Any]) -> None:
        migrated = self.migrate(data)
        self._ensure_database()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO project_memory(key, schema_version, data)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    data = excluded.data
                """,
                (
                    self.key,
                    int(migrated.get("schema_version") or CURRENT_SCHEMA_VERSION),
                    json.dumps(migrated, sort_keys=True),
                ),
            )

    def migrate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self.migrator.migrate(data)

    def _ensure_database(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_memory (
                    key TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    data TEXT NOT NULL
                )
                """)
            existing = {
                row[0] for row in conn.execute("SELECT version FROM schema_migrations")
            }
            for version in self._migration_versions():
                if version not in existing:
                    conn.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                    )

    @staticmethod
    def _migration_versions() -> Iterable[int]:
        return range(1, CURRENT_SCHEMA_VERSION + 1)
