"""
PlaybookManager — typed, retrieval-aware playbook storage and query.

Wraps the raw playbook dict in project memory with:
- Structured pattern storage (dicts with provenance, not raw strings)
- Similarity-based deduplication via MemoryRetriever so the playbook
  doesn't accumulate near-identical lessons across projects
- Per-category size cap (MAX_ENTRIES_PER_CATEGORY) so playbook growth
  is bounded
- A query() method that returns ranked relevant patterns for a task,
  which ContextBuilder uses instead of injecting the whole playbook

Backward-compatible: legacy string entries are read and injected as-is;
new entries are stored as dicts with a 'text' field.
"""

from __future__ import annotations

from typing import Iterable, Optional

from aider.memory.pattern_extractor import make_pattern, pattern_text
from aider.memory.retrieval import MemoryRetriever

# Hard cap on entries per playbook category.
# When exceeded during merge, the oldest entries are evicted.
MAX_ENTRIES_PER_CATEGORY = 50

# Similarity threshold above which a new pattern is considered a duplicate
# of an existing one and is not added.
DEDUP_THRESHOLD = 0.75

# Default number of patterns to return from query().
DEFAULT_QUERY_K = 5

# Known playbook categories.
PLAYBOOK_CATEGORIES = ("coding_standards", "ux_preferences", "deployment_gotchas")


class PlaybookManager:
    """
    Manages the structured playbook stored in CompanyStateManager.

    Args:
        state: A CompanyStateManager instance. PlaybookManager reads
               and writes through state.get_playbook() / state.save_playbook().
    """

    def __init__(self, state):
        self._state = state

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def merge_patterns(self, patterns: dict[str, list[dict]]) -> None:
        """
        Merge extracted patterns into the persisted playbook.

        For each category:
        1. Deduplicate against existing entries using MemoryRetriever.
        2. Append novel patterns.
        3. Evict oldest entries beyond MAX_ENTRIES_PER_CATEGORY.
        4. Persist.
        """
        playbook = self._state.get_playbook()

        for category in PLAYBOOK_CATEGORIES:
            existing = playbook.setdefault(category, [])
            if not isinstance(existing, list):
                existing = []
                playbook[category] = existing
            new_patterns = patterns.get(category, [])
            if not new_patterns:
                continue

            existing_texts = [pattern_text(e) for e in existing]
            for pattern in new_patterns:
                text = pattern_text(pattern)
                if not text:
                    continue
                if self._is_duplicate(text, existing_texts):
                    continue
                existing.append(pattern)
                existing_texts.append(text)

            # Enforce size cap — evict from the front (oldest first).
            if len(existing) > MAX_ENTRIES_PER_CATEGORY:
                playbook[category] = existing[-MAX_ENTRIES_PER_CATEGORY:]

        self._state.save_playbook(playbook)

    def append_raw(self, category: str, text: str) -> None:
        """
        Append a raw string pattern to *category* with dedup check.

        Legacy helper for callers that don't have a full pattern dict.
        Wraps the text in a minimal pattern dict before storing.
        """
        playbook = self._state.get_playbook()
        existing = playbook.setdefault(category, [])
        if not isinstance(existing, list):
            existing = []
            playbook[category] = existing
        existing_texts = [pattern_text(e) for e in existing]
        if self._is_duplicate(text, existing_texts):
            return
        existing.append(make_pattern(text=text, pattern_type="raw"))
        if len(existing) > MAX_ENTRIES_PER_CATEGORY:
            playbook[category] = existing[-MAX_ENTRIES_PER_CATEGORY:]
        self._state.save_playbook(playbook)

    # ------------------------------------------------------------------
    # Read / query path
    # ------------------------------------------------------------------

    def query(
        self,
        task_query: str,
        categories: Optional[Iterable[str]] = None,
        k: int = DEFAULT_QUERY_K,
        min_score: float = 0.05,
    ) -> dict[str, list[str]]:
        """
        Return the most relevant playbook patterns for *task_query*.

        Args:
            task_query: Free-text description of the current task.
            categories: Which playbook categories to search. Defaults to all.
            k:          Max patterns per category.
            min_score:  Minimum cosine similarity to include.

        Returns:
            Dict mapping category → list of injectable text strings,
            ordered by relevance descending. Empty categories are omitted.
        """
        playbook = self._state.get_playbook()
        target_cats = list(categories or PLAYBOOK_CATEGORIES)
        result: dict[str, list[str]] = {}

        for category in target_cats:
            entries = playbook.get(category, [])
            if not isinstance(entries, list) or not entries:
                continue
            texts = [pattern_text(e) for e in entries if pattern_text(e)]
            if not texts:
                continue

            if len(texts) <= k:
                # Small enough — return all without scoring.
                result[category] = texts
                continue

            retriever = MemoryRetriever(texts)
            top = retriever.top_k(task_query, k=k, min_score=min_score)
            if top:
                result[category] = [text for text, _ in top]
            else:
                # Nothing scored above threshold — return most recent as fallback.
                result[category] = texts[-k:]

        return result

    def get_all_texts(self, category: str) -> list[str]:
        """Return all injectable texts for *category* without retrieval."""
        entries = self._state.get_playbook().get(category, [])
        if not isinstance(entries, list):
            return []
        return [pattern_text(e) for e in entries if pattern_text(e)]

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _is_duplicate(text: str, existing_texts: list[str]) -> bool:
        """
        Return True if *text* is too similar to any entry in *existing_texts*.

        Uses MemoryRetriever cosine similarity. Returns False immediately
        if existing_texts is empty (nothing to deduplicate against).
        """
        if not existing_texts or not text:
            return False
        retriever = MemoryRetriever(existing_texts)
        scored = retriever.top_k(text, k=1, min_score=DEDUP_THRESHOLD)
        return bool(scored)
