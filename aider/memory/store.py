from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .project import ProjectMemory
from .records import MemoryQuery, MemoryRecord, ensure_record
from .scopes import scope_matches
from .visibility import filter_visible, validate_visibility


class MemoryStore:
    """Thin service wrapper over ``ProjectMemory`` for local-first records."""

    def __init__(self, project_memory: ProjectMemory):
        self.project_memory = project_memory
        self.project_memory._ensure_schema()

    def append_record(self, record: MemoryRecord | Dict[str, Any]) -> MemoryRecord:
        memory_record = ensure_record(record)
        validate_visibility(memory_record.visibility)
        memory = self._memory_namespace()
        records = memory.setdefault("records", [])
        records.append(memory_record.to_dict())
        self.project_memory.update({"memory": memory})
        self.project_memory.persist()
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
        if memory_query.limit is not None:
            return records[: max(0, int(memory_query.limit))]
        return records

    def get_record(self, record_id: str) -> Optional[MemoryRecord]:
        for item in self._record_dicts():
            if item.get("id") == record_id or item.get("record_id") == record_id:
                return MemoryRecord.from_dict(item)
        return None

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
        if query.text:
            needle = query.text.lower()
            filtered = [
                record
                for record in filtered
                if needle in str(record.content).lower()
                or needle in str(record.metadata).lower()
            ]
        return filtered
