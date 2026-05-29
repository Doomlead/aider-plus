from __future__ import annotations

import hashlib
import math
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Iterable

from .policy import RankingPolicy
from .ranking import compute_graph_boosts
from .records import MemoryQuery, MemoryRecord
from .retrieval import MemoryRetriever


class MemoryEmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class DeterministicHashEmbeddingProvider(MemoryEmbeddingProvider):
    """Local, dependency-free embedding provider for deterministic semantic ranking.

    This is intentionally not a model-backed embedding. It is a repository-local
    provider that produces stable hashed bag-of-words vectors so the vector index
    boundary can be exercised without network calls, secrets, or heavyweight
    dependencies.
    """

    def __init__(self, dimensions: int = 128):
        self.dimensions = max(8, int(dimensions))

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimensions
            sign = 1.0 if (value >> 1) & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm <= 0.0:
            return vector
        return [round(v / norm, 8) for v in vector]


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
    def __init__(self, *, text_builder=None, policy: RankingPolicy | None = None):
        self._text_builder = text_builder or self._default_text
        self._policy = policy or RankingPolicy()

    def name(self) -> str:
        return "local_tfidf"

    def rebuild(self, records: Iterable[MemoryRecord]) -> None:
        return None

    def add(self, record: MemoryRecord) -> None:
        return None

    def rank(
        self, records: list[MemoryRecord], query: MemoryQuery
    ) -> list[MemoryRecord]:
        if not query.text or len(records) <= 1:
            return records
        texts = [self._text_builder(record) for record in records]
        scored = MemoryRetriever(texts).score(query.text)
        score_map: dict[str, float] = {text: score for text, score in scored}
        graph_boosts = compute_graph_boosts(records)

        def total_score(record: MemoryRecord) -> float:
            relevance = score_map.get(self._text_builder(record), 0.0)
            reinforcement = float(record.reinforcement_score or 0.0)
            recency = self._recency_boost(record.last_used_at)
            graph = graph_boosts.get(record.id, 0.0)
            return (
                relevance
                + self._policy.reinforcement_weight * reinforcement
                + self._policy.recency_weight * recency
                + graph
            )

        return sorted(records, key=total_score, reverse=True)

    def health_check(self) -> dict[str, Any]:
        return {"backend": self.name(), "status": "ok", "degraded": False}

    @staticmethod
    def _default_text(record: MemoryRecord) -> str:
        return " ".join(
            str(part)
            for part in (record.kind, record.content, record.tags, record.metadata)
            if part
        )[:2000]

    @staticmethod
    def _recency_boost(last_used_at: str | None) -> float:
        if not last_used_at:
            return 0.0
        try:
            used = datetime.fromisoformat(str(last_used_at).replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        age_days = max(
            0.0, (datetime.now(timezone.utc) - used).total_seconds() / 86400.0
        )
        return 1.0 / (1.0 + age_days / 30.0)


class LocalVectorIndex(MemoryIndex, MemoryBackendAdapter):
    """Local vector index backed by a MemoryEmbeddingProvider.

    The index keeps embeddings in memory and combines cosine similarity with the
    same reinforcement/recency/graph signals as the deterministic TF-IDF path.
    """

    def __init__(
        self,
        *,
        embedding_provider: MemoryEmbeddingProvider | None = None,
        text_builder=None,
        policy: RankingPolicy | None = None,
    ):
        self._provider = embedding_provider or DeterministicHashEmbeddingProvider()
        self._text_builder = text_builder or LocalTFIDFIndex._default_text
        self._policy = policy or RankingPolicy()
        self._embeddings: dict[str, list[float]] = {}

    def name(self) -> str:
        return "local_vector"

    def rebuild(self, records: Iterable[MemoryRecord]) -> None:
        self._embeddings = {}
        for record in records:
            self.add(record)

    def add(self, record: MemoryRecord) -> None:
        self._embeddings[record.id] = self._provider.embed(self._text_builder(record))

    def rank(
        self, records: list[MemoryRecord], query: MemoryQuery
    ) -> list[MemoryRecord]:
        if not query.text or len(records) <= 1:
            return records
        query_embedding = self._provider.embed(query.text)
        graph_boosts = compute_graph_boosts(records)

        def total_score(record: MemoryRecord) -> float:
            embedding = self._embeddings.get(record.id)
            if embedding is None:
                embedding = self._provider.embed(self._text_builder(record))
                self._embeddings[record.id] = embedding
            relevance = _cosine(query_embedding, embedding)
            reinforcement = float(record.reinforcement_score or 0.0)
            recency = LocalTFIDFIndex._recency_boost(record.last_used_at)
            graph = graph_boosts.get(record.id, 0.0)
            return (
                relevance
                + self._policy.reinforcement_weight * reinforcement
                + self._policy.recency_weight * recency
                + graph
            )

        return sorted(records, key=total_score, reverse=True)

    def health_check(self) -> dict[str, Any]:
        return {
            "backend": self.name(),
            "status": "ok",
            "degraded": False,
            "embedding_provider": self._provider.__class__.__name__,
            "indexed_records": len(self._embeddings),
        }


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

    def rank(
        self, records: list[MemoryRecord], query: MemoryQuery
    ) -> list[MemoryRecord]:
        if self._conn is None:
            return self._fallback.rank(records, query)
        if not query.text or len(records) <= 1:
            return records
        ids = {record.id for record in records}
        try:
            cursor = self._conn.execute(
                "SELECT record_id, bm25(memory_fts) AS rank FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank",
                (query.text,),
            )
            order = {
                str(row[0]): idx
                for idx, row in enumerate(cursor.fetchall())
                if str(row[0]) in ids
            }
        except sqlite3.Error:
            self._conn = None
            return self._fallback.rank(records, query)
        if not order:
            return self._fallback.rank(records, query)
        fallback = self._fallback.rank(records, query)
        fallback_order = {record.id: idx for idx, record in enumerate(fallback)}
        return sorted(
            records,
            key=lambda record: (
                order.get(record.id, len(records)),
                fallback_order.get(record.id, len(records)),
            ),
        )

    def health_check(self) -> dict[str, Any]:
        if self._conn is None:
            return {
                "backend": self.name(),
                "status": "degraded",
                "degraded": True,
                "fallback": self._fallback.name(),
                "fallback_backend": self._fallback.name(),
                "error": self._init_error,
            }
        return {"backend": self.name(), "status": "ok", "degraded": False}


def _tokens(text: str) -> list[str]:
    token = ""
    out: list[str] = []
    for char in str(text or "").lower():
        if char.isalnum() or char in {"_", "-"}:
            token += char
        elif token:
            out.append(token)
            token = ""
    if token:
        out.append(token)
    return out


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[i] * right[i] for i in range(size))
    left_norm = math.sqrt(sum(v * v for v in left))
    right_norm = math.sqrt(sum(v * v for v in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
