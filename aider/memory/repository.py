from __future__ import annotations

import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

CURRENT_SCHEMA_VERSION = 8
logger = logging.getLogger(__name__)


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
        if version < 5:
            migrated = self._migrate_to_v5(migrated)
            version = 5
        if version < 6:
            migrated = self._migrate_to_v6(migrated)
            version = 6
        if version < 7:
            migrated = self._migrate_to_v7(migrated)
            version = 7
        if version < 8:
            migrated = self._migrate_to_v8(migrated)
            version = 8

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
        memory.setdefault("migration_log", [])
        memory.setdefault("corrupt_backup", [])
        return data

    def _migrate_to_v5(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Add memory metrics namespace, backups, and canonical memory values."""
        observability = data.get("observability")
        if not isinstance(observability, dict):
            observability = {}
            data["observability"] = observability
        observability.setdefault("memory_metrics", {})
        migration = data.get("migration")
        if not isinstance(migration, dict):
            migration = {}
            data["migration"] = migration
        migration.setdefault("last_schema_backup", None)
        memory = self._ensure_memory_namespace(data)
        already_ran = any(
            isinstance(item, dict) and int(item.get("to_version") or 0) >= 5
            for item in memory.get("migration_log", [])
        )
        if already_ran:
            return data
        records_processed = self._normalize_memory_records_for_v5(data)
        memory.setdefault("migration_log", []).append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "from_version": 4,
                "to_version": 5,
                "records_processed": records_processed,
            }
        )
        return data

    def _migrate_to_v6(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Final one-way rewrite of legacy visibility and channel_pair scopes."""
        from .visibility import VISIBILITY_PROJECT

        memory = self._ensure_memory_namespace(data)
        records = memory.get("records")
        if not isinstance(records, list):
            records = []
            memory["records"] = records

        rewrites = 0
        legacy_visibility = {"public", "team", "skill"}
        for item in records:
            if not isinstance(item, dict):
                continue
            visibility = item.get("visibility")
            if visibility in legacy_visibility:
                item["visibility"] = VISIBILITY_PROJECT
                rewrites += 1
            scope = str(item.get("scope") or "")
            if scope.startswith("channel_pair:"):
                item["scope"] = f"channel:{scope.split(':', 1)[1]}"
                rewrites += 1

        migration = data.setdefault("migration", {})
        if isinstance(migration, dict):
            migration["v6_legacy_rewrites"] = rewrites
        memory.setdefault("migration_log", []).append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "from_version": 5,
                "to_version": 6,
                "records_processed": len(records),
                "legacy_rewrites": rewrites,
            }
        )
        return data

    def _migrate_to_v7(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Add compaction marker namespace and metrics-safe defaults."""

        memory = self._ensure_memory_namespace(data)
        if not isinstance(memory.get("compaction_markers"), list):
            memory["compaction_markers"] = []
        memory.setdefault("migration_log", []).append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "from_version": 6,
                "to_version": 7,
                "records_processed": len(memory.get("records", [])),
                "compaction_markers_initialized": True,
            }
        )
        return data

    def _migrate_to_v8(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Materialize canonical record lookup indexes and migration metadata."""

        from .indexes import build_canonical_indexes

        memory = self._ensure_memory_namespace(data)
        records = memory.get("records", [])
        memory["indexes"] = build_canonical_indexes(
            records if isinstance(records, list) else []
        )
        memory.setdefault("migration_log", []).append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "from_version": 7,
                "to_version": 8,
                "records_processed": len(records) if isinstance(records, list) else 0,
                "indexes_initialized": sorted(["scope", "kind", "department", "skill"]),
            }
        )
        return data

    def _normalize_memory_records_for_v5(self, data: Dict[str, Any]) -> int:
        """Rewrite legacy aliases, validate records, and quarantine corrupt rows."""

        from .records import MemoryRecord

        memory = self._ensure_memory_namespace(data)
        records = memory.get("records")
        if not isinstance(records, list):
            records = []
            memory["records"] = records
        rewrites = 0
        valid_records: list[dict[str, Any]] = []
        corrupt_records: list[Any] = []
        lifted_keys = (
            "scope",
            "visibility",
            "kind",
            "created_at",
            "updated_at",
            "author",
            "department",
            "project_id",
            "thread_id",
            "channel_id",
            "channel",
            "user_id",
            "tags",
            "skill_evidence",
        )
        for item in records:
            if not isinstance(item, dict):
                corrupt_records.append(item)
                logger.warning(
                    "Skipping corrupt v5 memory record during migration: non-dict"
                )
                continue
            candidate = dict(item)
            metadata = (
                dict(candidate.get("metadata"))
                if isinstance(candidate.get("metadata"), dict)
                else {}
            )
            for key in lifted_keys:
                if key in metadata and key not in candidate:
                    candidate[key] = metadata.pop(key)
                    rewrites += 1
            if "channel" in candidate and "channel_id" not in candidate:
                candidate["channel_id"] = candidate.pop("channel")
                rewrites += 1
            scope = str(candidate.get("scope") or "")
            if scope.startswith("channel_pair:"):
                candidate["scope"] = f"channel:{scope.split(':', 1)[1]}"
                rewrites += 1
            candidate["metadata"] = metadata
            visibility = candidate.get("visibility")
            if visibility in {"public", "team", "skill"}:
                candidate["visibility"] = "project"
                rewrites += 1
            elif not visibility:
                candidate["visibility"] = "project"
                rewrites += 1
            try:
                record = MemoryRecord.from_dict(candidate)
                record.validate(allow_legacy_visibility=False)
            except Exception as exc:
                corrupt_records.append(item)
                logger.warning(
                    "Quarantining corrupt v5 memory record during migration: %s", exc
                )
                continue
            serialized = record.to_dict()
            valid_records.append(serialized)
            if serialized != item:
                rewrites += 1
        memory["records"] = valid_records
        if corrupt_records:
            self._append_corrupt_backup(memory, corrupt_records)
        migration = data.setdefault("migration", {})
        if isinstance(migration, dict):
            migration["v5_visibility_records_normalized"] = rewrites
            if corrupt_records:
                migration["v5_corrupt_records_quarantined"] = len(corrupt_records)
        return len(records)

    def _ensure_memory_namespace(self, data: Dict[str, Any]) -> Dict[str, Any]:
        memory = data.get("memory")
        if not isinstance(memory, dict):
            memory = {}
            data["memory"] = memory
        if not isinstance(memory.get("records"), list):
            memory["records"] = []
        if not isinstance(memory.get("threads"), list):
            memory["threads"] = []
        if not isinstance(memory.get("migration_log"), list):
            memory["migration_log"] = []
        if not isinstance(memory.get("corrupt_backup"), list):
            memory["corrupt_backup"] = []
        if not isinstance(memory.get("compaction_markers"), list):
            memory["compaction_markers"] = []
        if not isinstance(memory.get("indexes"), dict):
            memory["indexes"] = {}
        return memory

    def _append_corrupt_backup(
        self, memory: Dict[str, Any], records: list[Any]
    ) -> None:
        memory.setdefault("corrupt_backup", []).append(
            {
                "quarantined_at": datetime.now(timezone.utc).isoformat(),
                "reason": "v5 migration validation failed",
                "records": records,
            }
        )

    def _ensure_defaults(self, data: Dict[str, Any]) -> None:
        for key, value in self.defaults.items():
            if key not in data:
                data[key] = deepcopy(value)
        memory = data.get("memory")
        if not isinstance(memory, dict):
            memory = {}
            data["memory"] = memory
        if not isinstance(memory.get("records"), list):
            memory["records"] = []
        if not isinstance(memory.get("threads"), list):
            memory["threads"] = []
        if not isinstance(memory.get("migration_log"), list):
            memory["migration_log"] = []
        if not isinstance(memory.get("corrupt_backup"), list):
            memory["corrupt_backup"] = []
        if not isinstance(memory.get("compaction_markers"), list):
            memory["compaction_markers"] = []
        if not isinstance(memory.get("indexes"), dict):
            memory["indexes"] = {}
        observability = data.get("observability")
        if not isinstance(observability, dict):
            observability = {}
            data["observability"] = observability
        if not isinstance(observability.get("turns_per_phase"), dict):
            observability["turns_per_phase"] = {}
        if not isinstance(observability.get("token_usage_per_department"), dict):
            observability["token_usage_per_department"] = {}
        if not isinstance(observability.get("memory_metrics"), dict):
            observability["memory_metrics"] = {}


class MemoryMigrator:
    """Utility wrapper for future non-destructive schema migrations."""

    def __init__(self, migrator: ProjectMemoryMigrator):
        self.migrator = migrator

    def migrate_with_backup(self, data: Dict[str, Any]) -> Dict[str, Any]:
        original = deepcopy(data) if isinstance(data, dict) else {}
        migrated = self.migrator.migrate(data)
        if migrated != original:
            migration = migrated.setdefault("migration", {})
            if isinstance(migration, dict):
                migration["last_schema_backup"] = original
        return migrated

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
        self.memory_migrator = MemoryMigrator(self.migrator)

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
        return self.memory_migrator.migrate_with_backup(data)


class SQLiteMemoryRepository(MemoryRepository):
    """SQLite implementation storing the project memory document behind the same boundary."""

    def __init__(self, db_path: Path, defaults: Dict[str, Any], key: str = "default"):
        self.db_path = db_path
        self.key = key
        self.migrator = ProjectMemoryMigrator(defaults)
        self.memory_migrator = MemoryMigrator(self.migrator)

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
        return self.memory_migrator.migrate_with_backup(data)

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
