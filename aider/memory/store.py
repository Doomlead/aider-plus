from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from .index import LocalTFIDFIndex, MemoryIndex
from .metrics import summarize_memory_metrics
from .project import ProjectMemory
from .records import MemoryQuery, MemoryRecord, ensure_record, utc_now_iso
from .scopes import scope_matches
from .visibility import filter_visible, validate_visibility


class MemoryStore:
    """Thin service wrapper over ``ProjectMemory`` for local-first records."""

    def __init__(self, project_memory: ProjectMemory, index: MemoryIndex | None = None):
        self.project_memory = project_memory
        self.project_memory._ensure_schema()
        self.index = index or LocalTFIDFIndex()
        self.rebuild_index()

    def append_record(self, record: MemoryRecord | Dict[str, Any]) -> MemoryRecord:
        memory_record = ensure_record(record)
        memory_record.validate(allow_legacy_visibility=False)
        memory = self._memory_namespace()
        records = memory.setdefault("records", [])
        records.append(memory_record.to_dict())
        self.project_memory.update({"memory": memory})
        self.project_memory.persist()
        self.add_to_index(memory_record)
        self._increment_metric("memory_records_total_events")
        self._auto_compaction_check()
        return memory_record

    def query_records(
        self, query: MemoryQuery | None = None, **kwargs: Any
    ) -> list[MemoryRecord]:
        if query is not None and kwargs:
            raise ValueError("pass either a MemoryQuery or keyword filters, not both")
        memory_query = query or MemoryQuery.from_kwargs(**kwargs)
        records = [MemoryRecord.from_dict(item) for item in self._record_dicts()]
        records = self._filter_query(records, memory_query)
        records = filter_visible(records, memory_query)
        records = self.index.rank(records, memory_query)
        if memory_query.limit is not None:
            return records[: max(0, int(memory_query.limit))]
        return records

    def rebuild_index(self) -> None:
        self.index.rebuild(
            [MemoryRecord.from_dict(item) for item in self._record_dicts()]
        )

    def add_to_index(self, record: MemoryRecord) -> None:
        self.index.add(record)

    def get_record(self, record_id: str) -> Optional[MemoryRecord]:
        for item in self._record_dicts():
            if item.get("id") == record_id or item.get("record_id") == record_id:
                return MemoryRecord.from_dict(item)
        return None

    def reinforce_record(
        self, record_id: str, delta: int = 1
    ) -> Optional[dict[str, Any]]:
        for item in self._record_dicts():
            item_id = item.get("id") or item.get("record_id")
            if item_id != record_id:
                continue
            metadata = (
                item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            )
            metadata["reinforcement_count"] = int(
                metadata.get("reinforcement_count") or 0
            ) + max(0, int(delta))
            metadata["reinforcement_signal"] = int(
                metadata.get("reinforcement_signal") or 0
            ) + int(delta)
            item["metadata"] = metadata
            self.project_memory.update({"memory": self._memory_namespace()})
            self.project_memory.persist()
            return dict(item)
        return None

    def update_record_metadata(
        self, record_id: str, metadata: Dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        for item in self._record_dicts():
            item_id = item.get("id") or item.get("record_id")
            if item_id != record_id:
                continue
            item["metadata"] = dict(metadata or {})
            self.project_memory.update({"memory": self._memory_namespace()})
            self.project_memory.persist()
            return dict(item)
        return None

    def decay_stale_records(
        self, threshold_days: int = 30, max_records: int = 500
    ) -> int:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=max(1, int(threshold_days)))
        changed = 0
        records = list(self._record_dicts())[: max(1, int(max_records))]
        for item in records:
            timestamp = item.get("updated_at") or item.get("created_at")
            if not timestamp:
                continue
            try:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt >= cutoff:
                continue
            metadata = (
                item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            )
            signal = int(metadata.get("reinforcement_signal") or 0)
            if signal <= -5:
                continue
            metadata["reinforcement_signal"] = signal - 1
            metadata["decay_count"] = int(metadata.get("decay_count") or 0) + 1
            metadata["last_decay_at"] = now.isoformat()
            item["metadata"] = metadata
            changed += 1
        if changed:
            self.project_memory.update({"memory": self._memory_namespace()})
            self._increment_metric("decay_runs")
            self.project_memory.persist()
        return changed

    def prune_stale(self, threshold_days: int = 90, max_pruned: int = 500) -> int:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=max(1, int(threshold_days)))
        kept: list[dict[str, Any]] = []
        pruned = 0
        for item in self._record_dicts():
            ts = item.get("updated_at") or item.get("created_at")
            if ts and pruned < max_pruned:
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if dt < cutoff:
                        pruned += 1
                        continue
                except ValueError:
                    pass
            kept.append(item)
        if pruned:
            memory = self._memory_namespace()
            memory["records"] = kept
            self.project_memory.update({"memory": memory})
            self.project_memory.persist()
            self.rebuild_index()
        return pruned

    def enforce_limits(
        self, max_records_per_scope: int = 5000, max_total: int = 50000
    ) -> int:
        removed = 0
        records = list(self._record_dicts())
        if len(records) > max_total:
            drop = len(records) - max_total
            records = records[drop:]
            removed += drop
        by_scope: dict[str, list[dict[str, Any]]] = {}
        for item in records:
            by_scope.setdefault(str(item.get("scope") or "unknown"), []).append(item)
        trimmed: list[dict[str, Any]] = []
        for _, scoped in by_scope.items():
            if len(scoped) > max_records_per_scope:
                removed += len(scoped) - max_records_per_scope
                scoped = scoped[-max_records_per_scope:]
            trimmed.extend(scoped)
        if removed:
            trimmed.sort(
                key=lambda r: str(r.get("updated_at") or r.get("created_at") or "")
            )
            memory = self._memory_namespace()
            memory["records"] = trimmed
            self.project_memory.update({"memory": memory})
            self.project_memory.persist()
            self.rebuild_index()
        return removed

    def compact(
        self, threshold_days: int = 120, min_signal: int = -2, *, dry_run: bool = True
    ) -> int:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=max(1, int(threshold_days)))
        doomed: list[str] = []
        for item in self._record_dicts():
            ts = item.get("updated_at") or item.get("created_at")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                continue
            metadata = (
                item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            )
            signal = int(metadata.get("reinforcement_signal") or 0)
            if dt < cutoff and signal <= min_signal:
                doomed.append(str(item.get("id") or item.get("record_id") or ""))
        if dry_run or not doomed:
            return len(doomed)
        return self._drop_records(doomed)

    def repair(self, *, confirm: bool = False) -> dict[str, int]:
        repaired = {
            "invalid_records_removed": 0,
            "records_fixed": 0,
            "corrupt_records_backed_up": 0,
        }
        if not confirm:
            return repaired

        memory = self._memory_namespace()
        raw_records = memory.get("records", [])
        if not isinstance(raw_records, list):
            raw_records = []

        valid: list[dict[str, Any]] = []
        corrupt: list[Any] = []
        changed = False
        for item in raw_records:
            if not isinstance(item, dict):
                repaired["invalid_records_removed"] += 1
                corrupt.append(item)
                changed = True
                continue

            original = dict(item)
            candidate = dict(item)
            fixed = False
            if not (candidate.get("id") or candidate.get("record_id")):
                candidate["id"] = candidate["record_id"] = MemoryRecord(content=None).id
                fixed = True
            if not candidate.get("scope"):
                candidate["scope"] = "project"
                fixed = True
            if not candidate.get("visibility"):
                candidate["visibility"] = "project"
                fixed = True
            if not candidate.get("created_at"):
                candidate["created_at"] = utc_now_iso()
                fixed = True

            try:
                record = ensure_record(candidate)
                record.validate(allow_legacy_visibility=True)
                serialized = record.to_dict()
                valid.append(serialized)
                if fixed or serialized != original:
                    repaired["records_fixed"] += 1
                    changed = True
            except Exception:
                repaired["invalid_records_removed"] += 1
                corrupt.append(item)
                changed = True

        if corrupt:
            self._write_corrupt_backup(corrupt)
            repaired["corrupt_records_backed_up"] = len(corrupt)
        if changed:
            memory["records"] = valid
            self.project_memory.update({"memory": memory})
            self.project_memory.persist()
            self.rebuild_index()
        return repaired

    def get_metrics(self) -> dict[str, Any]:
        memory = self._memory_namespace()
        summary = summarize_memory_metrics(memory)
        observed = self.project_memory.data.get("observability", {}).get(
            "memory_metrics", {}
        )
        metrics = dict(observed) if isinstance(observed, dict) else {}
        metrics.update(summary)
        metrics.setdefault("recall_hit_rate", 0.0)
        metrics.setdefault("skill_success_rate", 0.0)
        metrics.setdefault("decay_runs", 0)
        metrics.setdefault("skill_proposals_created", 0)
        total = int(summary.get("memory_records_total", 0))
        max_total = int(metrics.get("max_total_limit", 50000))
        utilization = min(1.0, (total / max_total) if max_total > 0 else 1.0)
        stale_ratio = (
            (int(summary.get("stale_memory_count", 0)) / total) if total else 0.0
        )
        recall_hit_rate = float(metrics.get("recall_hit_rate", 0.0) or 0.0)
        score = 100.0 * (
            0.5 * (1.0 - utilization)
            + 0.3 * (1.0 - stale_ratio)
            + 0.2 * recall_hit_rate
        )
        metrics["memory_health_score"] = max(0.0, min(100.0, round(score, 1)))
        metrics["total_records"] = total
        metrics["stale_count"] = int(summary.get("stale_memory_count", 0))
        metrics["skill_evidence_coverage_pct"] = summary.get(
            "skill_evidence_coverage_pct", 0.0
        )
        return metrics

    def backfill_legacy_records(self) -> dict[str, Any]:
        memory = self._memory_namespace()
        records = memory.setdefault("records", [])
        existing_keys = self._existing_legacy_content_keys()
        stats = {
            "legacy_items_scanned": 0,
            "legacy_records_created": 0,
            "legacy_records_skipped_existing": 0,
        }

        for record in self.project_memory.data.get("audit_log", []):
            stats["legacy_items_scanned"] += 1
            canonical = self._canonical_from_audit_record(record)
            if canonical is None:
                continue
            key = canonical.metadata.get("legacy_content_key")
            if key in existing_keys:
                stats["legacy_records_skipped_existing"] += 1
                continue
            records.append(canonical.to_dict())
            existing_keys.add(str(key))
            stats["legacy_records_created"] += 1

        for category, items in self.project_memory.data.get("playbook", {}).items():
            if not isinstance(items, list):
                continue
            for index, entry in enumerate(items):
                stats["legacy_items_scanned"] += 1
                canonical = self._canonical_from_playbook_item(category, index, entry)
                key = canonical.metadata.get("legacy_content_key")
                if key in existing_keys:
                    stats["legacy_records_skipped_existing"] += 1
                    continue
                records.append(canonical.to_dict())
                existing_keys.add(str(key))
                stats["legacy_records_created"] += 1

        migration = self.project_memory.data.setdefault("migration", {})
        migration["v5_backfill_legacy_items_scanned"] = int(
            migration.get("v5_backfill_legacy_items_scanned", 0)
        ) + stats["legacy_items_scanned"]
        migration["v5_backfill_records_created"] = int(
            migration.get("v5_backfill_records_created", 0)
        ) + stats["legacy_records_created"]
        migration["v5_backfill_records_skipped_existing"] = int(
            migration.get("v5_backfill_records_skipped_existing", 0)
        ) + stats["legacy_records_skipped_existing"]
        migration["v5_backfill_last_run_at"] = utc_now_iso()

        self.project_memory.update({"memory": memory, "migration": migration})
        self.project_memory.persist()
        self.rebuild_index()
        return stats

    def _drop_records(self, ids: list[str]) -> int:
        doomed = set(ids)
        kept = [
            item
            for item in self._record_dicts()
            if str(item.get("id") or item.get("record_id") or "") not in doomed
        ]
        removed = len(list(self._record_dicts())) - len(kept)
        if removed:
            memory = self._memory_namespace()
            memory["records"] = kept
            self.project_memory.update({"memory": memory})
            self.project_memory.persist()
            self.rebuild_index()
        return removed

    def _write_corrupt_backup(self, records: list[Any]) -> None:
        backup_path = (
            self.project_memory._memory_path.parent / "memory_corrupt_backup.json"
        )
        existing: list[Any] = []
        if backup_path.exists():
            try:
                loaded = json.loads(backup_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing = loaded
            except json.JSONDecodeError:
                existing = []
        existing.append(
            {
                "repaired_at": datetime.now(timezone.utc).isoformat(),
                "records": records,
            }
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _increment_metric(self, key: str, amount: int = 1) -> None:
        observability = self.project_memory.data.setdefault("observability", {})
        metrics = observability.setdefault("memory_metrics", {})
        metrics[key] = int(metrics.get(key) or 0) + int(amount)

    def _auto_compaction_check(self) -> None:
        metrics = self.get_metrics()
        total = int(metrics.get("memory_records_total", 0))
        max_total = int(metrics.get("max_total_limit", 50000))
        if max_total <= 0:
            return
        if total >= int(max_total * 0.9):
            self._increment_metric("auto_compaction_suggested")

    def _memory_namespace(self) -> Dict[str, Any]:
        self.project_memory._ensure_schema()
        memory = self.project_memory.data.setdefault("memory", {})
        if not isinstance(memory, dict):
            memory = {"records": [], "threads": []}
            self.project_memory.data["memory"] = memory
        memory.setdefault("records", [])
        memory.setdefault("threads", [])
        memory.setdefault("migration_log", [])
        memory.setdefault("corrupt_backup", [])
        if not isinstance(memory["records"], list):
            memory["records"] = []
        if not isinstance(memory["threads"], list):
            memory["threads"] = []
        if not isinstance(memory["migration_log"], list):
            memory["migration_log"] = []
        if not isinstance(memory["corrupt_backup"], list):
            memory["corrupt_backup"] = []
        return memory

    def _record_dicts(self) -> Iterable[Dict[str, Any]]:
        for item in self._memory_namespace().get("records", []):
            if isinstance(item, dict):
                yield item

    def _existing_legacy_content_keys(self) -> set[str]:
        keys: set[str] = set()
        for item in self._record_dicts():
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                continue
            key = metadata.get("legacy_content_key")
            if key:
                keys.add(str(key))
        return keys

    def _canonical_from_audit_record(self, item: Any) -> MemoryRecord | None:
        if not isinstance(item, dict):
            return None
        project_id = str(item.get("project_id") or self.project_memory.repo_path)
        department = str(item.get("department") or "orchestrator")
        timestamp = str(item.get("timestamp") or utc_now_iso())
        legacy_path = f"audit_log:{item.get('event_id') or timestamp}"
        payload = {
            "event_id": item.get("event_id"),
            "event_type": item.get("event_type"),
            "payload_summary": item.get("payload_summary"),
            "metadata": item.get("metadata"),
            "timestamp": item.get("timestamp"),
        }
        return MemoryRecord(
            kind="audit_summary",
            scope=f"project:{project_id}",
            visibility="project",
            project_id=project_id,
            department=department,
            channel_id="audit_log",
            created_at=timestamp,
            content=payload,
            metadata={
                "source": {"legacy_path": legacy_path},
                "legacy_content_key": self._legacy_content_key("audit_log", legacy_path, payload),
            },
        )

    def _canonical_from_playbook_item(
        self, category: Any, index: int, entry: Any
    ) -> MemoryRecord:
        project_id = str(self.project_memory.data.get("project_id") or self.project_memory.repo_path)
        payload = {"category": str(category), "entry": entry, "index": int(index)}
        legacy_path = f"playbook:{category}:{index}"
        return MemoryRecord(
            kind="playbook" if str(category) == "coding_standards" else "pattern",
            scope=f"project:{project_id}",
            visibility="project",
            project_id=project_id,
            department="orchestrator",
            channel_id="playbook",
            content=payload,
            metadata={
                "source": {"legacy_path": legacy_path},
                "legacy_content_key": self._legacy_content_key("playbook", legacy_path, payload),
            },
        )

    def _legacy_content_key(self, bucket: str, legacy_path: str, content: Any) -> str:
        payload = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
        material = f"{bucket}|{legacy_path}|{payload}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _filter_query(
        self, records: list[MemoryRecord], query: MemoryQuery
    ) -> list[MemoryRecord]:
        filtered = records
        if query.scope:
            filtered = [
                record
                for record in filtered
                if scope_matches(record.scope, query.scope)
            ]
        if query.visibility:
            requested_visibility = validate_visibility(query.visibility)
            filtered = [
                record
                for record in filtered
                if validate_visibility(record.visibility) == requested_visibility
            ]
        if query.kind:
            filtered = [record for record in filtered if record.kind == query.kind]
        if query.tags:
            required = set(query.tags)
            filtered = [record for record in filtered if required.issubset(record.tags)]
        return filtered
