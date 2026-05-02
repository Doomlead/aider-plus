from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentMemory:
    """Lightweight memory container for agent orchestration.

    - Working memory: per-session turn/tool snippets retained in memory and mirrored to SQLite.
    - Project memory: repo-scoped facts and conventions persisted to .aider/agent_memory.json.
    - Episodic memory: cross-session summaries in SQLite (for later consolidation jobs).
    """

    session_id: str
    repo_root: str | None = None
    db_path: Path | None = None
    max_working_turns: int = 12
    working_turns: list[dict[str, Any]] = field(default_factory=list)
    project_memory: dict[str, Any] = field(default_factory=dict)
    error_flags: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.db_path is None:
            self.db_path = Path(".aider") / "agent_memory.sqlite3"

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._load_project_memory()
        self._load_recent_working_turns()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS working_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    meta_json TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodic_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_root TEXT,
                    session_id TEXT,
                    summary TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _project_memory_path(self) -> Path | None:
        if not self.repo_root:
            return None
        return Path(self.repo_root) / ".aider" / "agent_memory.json"

    def _load_project_memory(self):
        path = self._project_memory_path()
        if not path or not path.exists():
            self.project_memory = {}
            return

        try:
            self.project_memory = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.project_memory = {}

    def save_project_memory(self):
        path = self._project_memory_path()
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.project_memory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _load_recent_working_turns(self):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, meta_json
                FROM working_memory
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (self.session_id, self.max_working_turns),
            ).fetchall()

        rows = list(reversed(rows))
        self.working_turns = []
        for role, content, meta_json in rows:
            meta = {}
            if meta_json:
                try:
                    meta = json.loads(meta_json)
                except json.JSONDecodeError:
                    meta = {}
            self.working_turns.append({"role": role, "content": content, "meta": meta})

    def add_working_turn(self, *, role: str, content: str, meta: dict[str, Any] | None = None):
        meta = meta or {}
        entry = {"role": role, "content": content, "meta": meta}
        self.working_turns.append(entry)
        self.working_turns = self.working_turns[-self.max_working_turns :]

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO working_memory(session_id, role, content, meta_json) VALUES(?, ?, ?, ?)",
                (self.session_id, role, content, json.dumps(meta)),
            )

    def build_running_context(self, *, user_intent: str, max_items: int = 8) -> str:
        items = self.working_turns[-max_items:]
        lines: list[str] = []
        for item in items:
            role = item.get("role", "unknown")
            content = str(item.get("content", "")).strip().replace("\n", " ")
            if not content:
                continue
            lines.append(f"- {role}: {content[:400]}")

        if self.project_memory:
            project_bits = json.dumps(self.project_memory, separators=(",", ":"))[:1200]
            lines.append(f"- project_memory: {project_bits}")

        if self.error_flags:
            lines.append("- flagged_errors: " + " | ".join(self.error_flags[-4:]))

        lines.append(f"- user_intent: {user_intent[:800]}")
        return "Running context summary:\n" + "\n".join(lines)

    def flag_error(self, message: str):
        msg = str(message).strip()
        if not msg:
            return
        self.error_flags.append(msg)
        self.error_flags = self.error_flags[-20:]
        self.add_working_turn(role="system", content=f"ERROR FLAG: {msg}", meta={"kind": "error_flag"})

    def add_episodic_summary(self, *, summary: str, metadata: dict[str, Any] | None = None):
        metadata = metadata or {}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO episodic_memory(repo_root, session_id, summary, metadata_json) VALUES(?, ?, ?, ?)",
                (self.repo_root, self.session_id, summary, json.dumps(metadata)),
            )
