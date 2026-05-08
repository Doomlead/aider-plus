"""
Lightweight memory retrieval for context injection.

Scores text chunks against a query using TF-IDF cosine similarity.
No external ML dependencies — uses only the stdlib math module.

Usage:
    from aider.memory.retrieval import MemoryRetriever

    retriever = MemoryRetriever(chunks)          # list[str]
    top = retriever.top_k(query, k=5, min_score=0.05)
    # returns list[tuple[str, float]] sorted by score descending
"""

from __future__ import annotations

import math
import re
from typing import Iterable


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, drop empty tokens."""
    return [tok for tok in re.split(r"[^a-z0-9]+", text.lower()) if tok]


def _tf(tokens: list[str]) -> dict[str, float]:
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for tok in tokens:
        counts[tok] = counts.get(tok, 0) + 1
    n = len(tokens)
    return {tok: count / n for tok, count in counts.items()}


def _idf(tokenized_docs: list[list[str]]) -> dict[str, float]:
    n = len(tokenized_docs)
    if n == 0:
        return {}
    df: dict[str, int] = {}
    for tokens in tokenized_docs:
        for tok in set(tokens):
            df[tok] = df.get(tok, 0) + 1
    return {tok: math.log((n + 1) / (count + 1)) + 1.0 for tok, count in df.items()}


def _tfidf_vector(tf: dict[str, float], idf: dict[str, float]) -> dict[str, float]:
    return {tok: tf_val * idf.get(tok, 1.0) for tok, tf_val in tf.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a.get(tok, 0.0) * val for tok, val in b.items())
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class MemoryRetriever:
    """
    Scores a fixed corpus of text chunks against arbitrary queries.

    Construct once per context-build call with the chunks you want to
    rank. Calling top_k() is cheap (pure dict arithmetic).

    Args:
        chunks: Sequence of text strings to score.
    """

    def __init__(self, chunks: Iterable[str]):
        self._chunks: list[str] = [str(c) for c in chunks if c]
        self._tokenized: list[list[str]] = [_tokenize(c) for c in self._chunks]
        self._idf: dict[str, float] = _idf(self._tokenized)
        self._doc_vectors: list[dict[str, float]] = [
            _tfidf_vector(_tf(tokens), self._idf) for tokens in self._tokenized
        ]

    def score(self, query: str) -> list[tuple[str, float]]:
        """Return (chunk, score) pairs for every chunk, unsorted."""
        q_tokens = _tokenize(query)
        q_vec = _tfidf_vector(_tf(q_tokens), self._idf)
        return [
            (chunk, _cosine(q_vec, doc_vec))
            for chunk, doc_vec in zip(self._chunks, self._doc_vectors)
        ]

    def top_k(
        self,
        query: str,
        k: int = 5,
        min_score: float = 0.05,
    ) -> list[tuple[str, float]]:
        """
        Return up to *k* (chunk, score) pairs with score >= min_score,
        sorted by score descending.

        Args:
            query:     The text to score against (task description, PRD excerpt, etc.)
            k:         Maximum chunks to return.
            min_score: Minimum cosine similarity to include. Chunks scoring below
                       this are discarded entirely. Set to 0.0 to disable filtering.
        """
        scored = [
            (chunk, score) for chunk, score in self.score(query) if score >= min_score
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
