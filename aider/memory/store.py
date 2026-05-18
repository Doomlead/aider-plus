from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .index import LocalTFIDFIndex, MemoryIndex
from .metrics import summarize_memory_metrics
from .project import ProjectMemory
from .records import MemoryQuery, MemoryRecord, ensure_record
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
        validate_visibility(memory_record.visibility)
        memory = self._memory_namespace()
        records = memory.setdefault("records", [])
        records.append(memory_record.to_dict())
        self.project_memory.update({"memory": memory})
        self.project_memory.persist()
        self.add_to_index(memory_record)
        self._increment_metric("memory_records_total_events")
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
        self.index.rebuild([MemoryRecord.from_dict(item) for item in self._record_dicts()])

    def add_to_index(self, record: MemoryRecord) -> None:
        self.index.add(record)
    def get_record(self, record_id: str) -> Optional[MemoryRecord]:
        for item in self._record_dicts():
            if item.get("id") == record_id or item.get("record_id") == record_id:
                return MemoryRecord.from_dict(item)
        return None

    def reinforce_record(self, record_id: str, delta: int = 1) -> Optional[dict[str, Any]]:
        for item in self._record_dicts():
            item_id = item.get("id") or item.get("record_id")
            if item_id != record_id:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            metadata["reinforcement_count"] = int(metadata.get("reinforcement_count") or 0) + max(0, int(delta))
            metadata["reinforcement_signal"] = int(metadata.get("reinforcement_signal") or 0) + int(delta)
            item["metadata"] = metadata
            self.project_memory.update({"memory": self._memory_namespace()})
            self.project_memory.persist()
            return dict(item)
        return None

    def update_record_metadata(self, record_id: str, metadata: Dict[str, Any]) -> Optional[dict[str, Any]]:
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
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
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

    def enforce_limits(self, max_records_per_scope: int = 5000, max_total: int = 50000) -> int:
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
            trimmed.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""))
            memory = self._memory_namespace()
            memory["records"] = trimmed
            self.project_memory.update({"memory": memory})
            self.project_memory.persist()
            self.rebuild_index()
        return removed

    def compact(self, threshold_days: int = 120, min_signal: int = -2, *, dry_run: bool = True) -> int:
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
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            signal = int(metadata.get("reinforcement_signal") or 0)
            if dt < cutoff and signal <= min_signal:
                doomed.append(str(item.get("id") or item.get("record_id") or ""))
        if dry_run or not doomed:
            return len(doomed)
        return self._drop_records(doomed)

    def repair(self, *, confirm: bool = False) -> dict[str, int]:
        repaired = {"invalid_records_removed": 0}
        if not confirm:
            return repaired
        valid: list[dict[str, Any]] = []
        for item in self._record_dicts():
            try:
                ensure_record(item)
                valid.append(item)
            except Exception:
                repaired["invalid_records_removed"] += 1
        if repaired["invalid_records_removed"]:
            memory = self._memory_namespace()
            memory["records"] = valid
            self.project_memory.update({"memory": memory})
            self.project_memory.persist()
            self.rebuild_index()
        return repaired

    def get_metrics(self) -> dict[str, Any]:
        memory = self._memory_namespace()
        summary = summarize_memory_metrics(memory)
        observed = self.project_memory.data.get("observability", {}).get("memory_metrics", {})
        metrics = dict(observed) if isinstance(observed, dict) else {}
        metrics.update(summary)
        metrics.setdefault("recall_hit_rate", 0.0)
        metrics.setdefault("skill_success_rate", 0.0)
        metrics.setdefault("decay_runs", 0)
        metrics.setdefault("skill_proposals_created", 0)
        return metrics

    def _drop_records(self, ids: list[str]) -> int:
        doomed = set(ids)
        kept = [item for item in self._record_dicts() if str(item.get("id") or item.get("record_id") or "") not in doomed]
        removed = len(list(self._record_dicts())) - len(kept)
        if removed:
            memory = self._memory_namespace()
            memory["records"] = kept
            self.project_memory.update({"memory": memory})
            self.project_memory.persist()
            self.rebuild_index()
        return removed

    def _increment_metric(self, key: str, amount: int = 1) -> None:
        observability = self.project_memory.data.setdefault("observability", {})
        metrics = observability.setdefault("memory_metrics", {})
        metrics[key] = int(metrics.get(key) or 0) + int(amount)

    def _memory_namespace(self) -> Dict[str, Any]:
        self.project_memory._ensure_schema()
        memory = self.project_memory.data.setdefault("memory", {})
        if not isinstance(memory, dict):
            memory = {"records": [], "threads": []}
            self.project_memory.data["memory"] = memory
        memory.setdefault("records", [])
        memory.setdefault("threads", [])
        if not isinstance(memory["records"], list):
            memory["records"] = []
        if not isinstance(memory["threads"], list):
            memory["threads"] = []
        return memory

    def _record_dicts(self) -> Iterable[Dict[str, Any]]:
        for item in self._memory_namespace().get("records", []):
            if isinstance(item, dict):
                yield item

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
            filtered = [
                record for record in filtered if record.visibility == query.visibility
            ]
        if query.kind:
            filtered = [record for record in filtered if record.kind == query.kind]
        if query.tags:
            required = set(query.tags)
            filtered = [record for record in filtered if required.issubset(record.tags)]
        return filtered
