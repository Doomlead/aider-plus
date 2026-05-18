from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

from .records import MemoryQuery, MemoryRecord
from .retrieval import MemoryRetriever


class MemoryEmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class MemoryBackendAdapter(ABC):
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError


class MemoryIndex(ABC):
    @abstractmethod
    def rebuild(self, records: Iterable[MemoryRecord]) -> None:
        raise NotImplementedError

    @abstractmethod
    def add(self, record: MemoryRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def rank(
        self, records: list[MemoryRecord], query: MemoryQuery
    ) -> list[MemoryRecord]:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        raise NotImplementedError


class LocalTFIDFIndex(MemoryIndex, MemoryBackendAdapter):
    def __init__(self, *, text_builder=None):
        self._text_builder = text_builder or self._default_text

    def name(self) -> str:
        return "local_tfidf"

    def rebuild(self, records: Iterable[MemoryRecord]) -> None:
        return None

    def add(self, record: MemoryRecord) -> None:
        return None

    def rank(self, records: list[MemoryRecord], query: MemoryQuery) -> list[MemoryRecord]:
        if not query.text or len(records) <= 1:
            return records
        texts = [self._text_builder(record) for record in records]
        scored = MemoryRetriever(texts).score(query.text)
        score_map: dict[str, float] = {text: score for text, score in scored}
        return sorted(
            records,
            key=lambda record: score_map.get(self._text_builder(record), 0.0),
            reverse=True,
        )

    def health_check(self) -> dict[str, Any]:
        return {"backend": self.name(), "status": "ok", "degraded": False}

    @staticmethod
    def _default_text(record: MemoryRecord) -> str:
        return " ".join(
            str(part)
            for part in (record.kind, record.content, record.tags, record.metadata)
            if part
        )[:2000]


class SQLiteFTSIndex(MemoryIndex, MemoryBackendAdapter):
    def __init__(self, db_path: str | Path = ":memory:", *, text_builder=None):
        self.db_path = str(db_path)
        self._text_builder = text_builder or LocalTFIDFIndex._default_text
        self._fallback = LocalTFIDFIndex(text_builder=self._text_builder)
        self._conn: sqlite3.Connection | None = None
        self._init_error: str | None = None
        try:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(record_id, content)"
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            self._init_error = str(exc)
            if self._conn is not None:
                self._conn.close()
            self._conn = None

    def name(self) -> str:
        return "sqlite_fts"

    def rebuild(self, records: Iterable[MemoryRecord]) -> None:
        if self._conn is None:
            self._fallback.rebuild(records)
            return
        try:
            self._conn.execute("DELETE FROM memory_fts")
            self._conn.executemany(
                "INSERT INTO memory_fts(record_id, content) VALUES(?, ?)",
                [(record.id, self._text_builder(record)) for record in records],
            )
            self._conn.commit()
        except sqlite3.Error:
            self._conn = None

    def add(self, record: MemoryRecord) -> None:
        if self._conn is None:
            self._fallback.add(record)
            return
        try:
            self._conn.execute(
                "INSERT INTO memory_fts(record_id, content) VALUES(?, ?)",
                (record.id, self._text_builder(record)),
            )
            self._conn.commit()
        except sqlite3.Error:
            self._conn = None

    def rank(self, records: list[MemoryRecord], query: MemoryQuery) -> list[MemoryRecord]:
        if self._conn is None:
            return self._fallback.rank(records, query)
        if not query.text or len(records) <= 1:
            return records
        record_map = {record.id: record for record in records}
        placeholders = ",".join("?" for _ in records)
        sql = (
            "SELECT record_id, bm25(memory_fts) as score "
            "FROM memory_fts WHERE memory_fts MATCH ? "
            f"AND record_id IN ({placeholders}) ORDER BY score ASC"
        )
        try:
            rows = self._conn.execute(sql, [query.text, *record_map.keys()]).fetchall()
        except sqlite3.Error:
            self._conn = None
            return self._fallback.rank(records, query)
        ordered = [record_map[row[0]] for row in rows if row[0] in record_map]
        seen = {record.id for record in ordered}
        for record in records:
            if record.id not in seen:
                ordered.append(record)
        return ordered

    def health_check(self) -> dict[str, Any]:
        if self._conn is None:
            return {
                "backend": self.name(),
                "status": "degraded",
                "degraded": True,
                "fallback_backend": self._fallback.name(),
                "error": self._init_error or "sqlite unavailable; using fallback",
            }
        return {"backend": self.name(), "status": "ok", "degraded": False}
