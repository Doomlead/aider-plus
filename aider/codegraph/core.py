from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from aider.io import InputOutput
from aider.repomap import RepoMap

DEFAULT_DB = Path(".aider") / "codegraph" / "graph.sqlite"
SKIP_DIRS = {
    ".git",
    ".aider",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
}
CODE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".php",
    ".cs",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".hpp",
    ".kt",
    ".swift",
    ".scala",
    ".ex",
    ".exs",
    ".vue",
    ".svelte",
}
TEST_PAT = re.compile(
    r"(^|/)(tests?|spec)(/|$)|(^|/).*(_test|_spec|\.test|\.spec)\.", re.I
)
ROUTE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "python",
        re.compile(
            r"@(?:app|router|blueprint)\.(get|post|put|patch|delete|route)\(\s*['\"]([^'\"]+)['\"]"
        ),
    ),
    (
        "django",
        re.compile(r"\b(?:path|re_path)\(\s*(?:r)?['\"]([^'\"]+)['\"]"),
    ),
    (
        "javascript",
        re.compile(
            r"(?:app|router)\.(get|post|put|patch|delete|use|all)\(\s*['\"]([^'\"]+)['\"]"
        ),
    ),
    (
        "nestjs",
        re.compile(r"@(Get|Post|Put|Patch|Delete|All)\(\s*['\"]([^'\"]*)['\"]?\s*\)"),
    ),
    ("rails", re.compile(r"\b(get|post|put|patch|delete)\s+['\"]([^'\"]+)['\"]")),
)
FILE_ROUTE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "nextjs",
        re.compile(r"^(?:app|pages)/(.+?)(?:/(?:page|route))?\.(?:js|jsx|ts|tsx)$"),
    ),
    ("nuxt", re.compile(r"^pages/(.+?)\.(?:vue|js|ts)$")),
    ("sveltekit", re.compile(r"^src/routes/(.+?)/?\+page\.(?:svelte|js|ts)$")),
)


@dataclass(frozen=True)
class CodeGraphStatus:
    repo_path: str
    db_path: str
    files: int
    symbols: int
    edges: int
    routes: int
    stale_files: int
    indexed_at: str | None


class CodeGraph:
    """Persistent SQLite-backed code intelligence graph for Aider Plus.

    The graph intentionally reuses Aider's existing tree-sitter tag extraction instead of depending
    on CodeGraph. Definitions and references are persisted, linked into symbol/file edges, enriched
    with lightweight framework route detection, and exposed through search/context/impact APIs.
    """

    def __init__(
        self,
        repo_path: str | os.PathLike[str] | None = None,
        db_path: str | os.PathLike[str] | None = None,
    ):
        self.repo_path = Path(repo_path or os.getcwd()).resolve()
        self.db_path = (
            Path(db_path).resolve() if db_path else self.repo_path / DEFAULT_DB
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.io = InputOutput(yes=True, pretty=False)
        self.repomap = RepoMap(root=str(self.repo_path), io=self.io)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS files(
                    path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    indexed_at REAL NOT NULL,
                    language TEXT,
                    is_test INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS symbols(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    UNIQUE(name, kind, file_path, line)
                );
                CREATE TABLE IF NOT EXISTS refs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    line INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edges(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    edge_type TEXT NOT NULL,
                    src_file TEXT NOT NULL,
                    dst_file TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    UNIQUE(edge_type, src_file, dst_file, symbol)
                );
                CREATE TABLE IF NOT EXISTS routes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    framework TEXT NOT NULL,
                    method TEXT,
                    route TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    UNIQUE(framework, method, route, file_path, line)
                );
                CREATE TABLE IF NOT EXISTS graph_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5(name, kind, file_path);
                CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
                CREATE INDEX IF NOT EXISTS idx_refs_name ON refs(name);
                CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_file);
                CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_file);
                """)

    def discover_files(self) -> list[Path]:
        tracked = self._git_files()
        if tracked:
            return [
                self.repo_path / path
                for path in tracked
                if self._is_code_file(self.repo_path / path)
            ]
        files: list[Path] = []
        for root, dirs, names in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in names:
                path = Path(root) / name
                if self._is_code_file(path):
                    files.append(path)
        return sorted(files)

    def index(
        self,
        *,
        force: bool = False,
        paths: Iterable[str | os.PathLike[str]] | None = None,
    ) -> dict[str, Any]:
        candidates = [Path(p) for p in paths] if paths else self.discover_files()
        indexed = skipped = removed = 0
        seen: set[str] = set()
        with self._connect() as conn:
            for path in candidates:
                abs_path = path if path.is_absolute() else self.repo_path / path
                if not abs_path.exists() or not self._is_code_file(abs_path):
                    continue
                rel = self._rel(abs_path)
                seen.add(rel)
                stat = abs_path.stat()
                row = conn.execute(
                    "SELECT mtime, size FROM files WHERE path=?", (rel,)
                ).fetchone()
                if (
                    not force
                    and row
                    and float(row["mtime"]) == stat.st_mtime
                    and int(row["size"]) == stat.st_size
                ):
                    skipped += 1
                    continue
                self._index_file(conn, abs_path, rel, stat)
                indexed += 1
            if paths is None:
                existing = {row[0] for row in conn.execute("SELECT path FROM files")}
                for rel in existing - seen:
                    self._delete_file(conn, rel)
                    removed += 1
            conn.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES('indexed_at', ?)",
                (str(time.time()),),
            )
        return {
            "indexed": indexed,
            "skipped": skipped,
            "removed": removed,
            "db_path": str(self.db_path),
        }

    def sync(self) -> dict[str, Any]:
        return self.index(force=False)

    def status(self) -> CodeGraphStatus:
        with self._connect() as conn:
            files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            routes = conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
            indexed_at = conn.execute(
                "SELECT value FROM graph_meta WHERE key='indexed_at'"
            ).fetchone()
            stale = 0
            for row in conn.execute("SELECT path, mtime, size FROM files"):
                path = self.repo_path / row["path"]
                if (
                    not path.exists()
                    or path.stat().st_mtime != row["mtime"]
                    or path.stat().st_size != row["size"]
                ):
                    stale += 1
        return CodeGraphStatus(
            str(self.repo_path),
            str(self.db_path),
            files,
            symbols,
            edges,
            routes,
            stale,
            indexed_at[0] if indexed_at else None,
        )

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT name, kind, file_path, bm25(symbol_fts) AS score FROM symbol_fts WHERE symbol_fts MATCH ? ORDER BY score LIMIT ?",
                    (self._fts_query(query), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    "SELECT name, kind, file_path, 0 AS score FROM symbol_fts WHERE name LIKE ? OR file_path LIKE ? LIMIT ?",
                    (f"%{query}%", f"%{query}%", limit),
                ).fetchall()
            return [dict(row) for row in rows]

    def node(self, name: str) -> dict[str, Any]:
        with self._connect() as conn:
            defs = [
                dict(r)
                for r in conn.execute(
                    "SELECT name, kind, file_path, line FROM symbols WHERE name=? ORDER BY file_path, line",
                    (name,),
                )
            ]
            refs = [
                dict(r)
                for r in conn.execute(
                    "SELECT name, file_path, line FROM refs WHERE name=? ORDER BY file_path, line LIMIT 100",
                    (name,),
                )
            ]
        return {"name": name, "definitions": defs, "references": refs}

    def callers(self, name_or_file: str, *, limit: int = 25) -> list[dict[str, Any]]:
        return self._edge_query(name_or_file, incoming=True, limit=limit)

    def callees(self, name_or_file: str, *, limit: int = 25) -> list[dict[str, Any]]:
        return self._edge_query(name_or_file, incoming=False, limit=limit)

    def impact(
        self, target: str, *, depth: int = 2, limit: int = 100
    ) -> dict[str, Any]:
        start_files = self._resolve_target_files(target)
        impacted: dict[str, int] = {path: 0 for path in start_files}
        frontier = set(start_files)
        with self._connect() as conn:
            for level in range(1, max(1, depth) + 1):
                if not frontier:
                    break
                placeholders = ",".join("?" for _ in frontier)
                rows = conn.execute(
                    f"SELECT src_file, dst_file, symbol FROM edges WHERE dst_file IN ({placeholders})",
                    tuple(frontier),
                ).fetchall()
                next_frontier = set()
                for row in rows:
                    src = row["src_file"]
                    if src not in impacted:
                        impacted[src] = level
                        next_frontier.add(src)
                frontier = next_frontier
        files = [
            {"path": path, "distance": dist, "is_test": self._is_test_path(path)}
            for path, dist in sorted(impacted.items(), key=lambda i: (i[1], i[0]))[
                :limit
            ]
        ]
        return {
            "target": target,
            "start_files": sorted(start_files),
            "files": files,
            "routes": self.routes_for_files([f["path"] for f in files]),
        }

    def affected_tests(
        self, changed_files: Iterable[str] | None = None, *, depth: int = 2
    ) -> dict[str, Any]:
        changed = list(changed_files or self._git_changed_files())
        impacted: dict[str, dict[str, Any]] = {}
        graph_tests: set[str] = set()
        for path in changed:
            for item in self.impact(path, depth=depth)["files"]:
                impacted[item["path"]] = item
                if item.get("is_test") or self._is_test_path(item["path"]):
                    graph_tests.add(item["path"])

        inferred_tests = self._infer_test_paths(changed, impacted.keys())
        tests = sorted(dict.fromkeys([*sorted(graph_tests), *inferred_tests]))
        confidence = "high" if graph_tests else "medium" if inferred_tests else "low"
        return {
            "changed_files": changed,
            "affected_tests": tests,
            "graph_matched_tests": sorted(graph_tests),
            "inferred_test_suggestions": inferred_tests,
            "suggested_commands": self._test_commands(tests),
            "confidence": confidence,
            "routes": self.routes_for_files([*changed, *impacted.keys()]),
            "impacted_files": sorted(impacted),
            "untested_impacted_files": sorted(
                path
                for path in impacted
                if not self._is_test_path(path)
                and not self._has_nearby_test(path, tests)
            ),
        }

    def _infer_test_paths(
        self, changed_files: Iterable[str], impacted_files: Iterable[str]
    ) -> list[str]:
        """Find existing tests related by naming/path conventions.

        Graph edges are strongest, but many repos do not import production code from
        tests directly. This fallback keeps suggestions useful by matching indexed
        test files against changed/impacted module stems and path tokens.
        """
        candidate_files = {str(path) for path in changed_files if path}
        candidate_files.update(str(path) for path in impacted_files if path)
        with self._connect() as conn:
            indexed_tests = [
                row[0]
                for row in conn.execute(
                    "SELECT path FROM files WHERE is_test=1 ORDER BY path"
                ).fetchall()
            ]
        if not candidate_files or not indexed_tests:
            return []

        needles: set[str] = set()
        for file_path in candidate_files:
            path = Path(file_path)
            stem = re.sub(r"(?:\.test|\.spec|_test|_spec)$", "", path.stem)
            if stem and stem not in {"index", "__init__"}:
                needles.add(stem.lower())
            parts = [part.lower() for part in path.with_suffix("").parts]
            needles.update(part for part in parts if part not in {"src", "app", "lib"})

        suggestions = []
        for test_path in indexed_tests:
            normalized = test_path.lower().replace("-", "_")
            if any(needle.replace("-", "_") in normalized for needle in needles):
                suggestions.append(test_path)
        return sorted(dict.fromkeys(suggestions))

    @staticmethod
    def _test_commands(tests: Iterable[str]) -> list[str]:
        tests = [str(test) for test in tests if test]
        if not tests:
            return []
        commands: list[str] = []
        py_tests = [test for test in tests if test.endswith(".py")]
        js_tests = [
            test
            for test in tests
            if test.endswith((".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"))
        ]
        if py_tests:
            commands.append(
                "pytest "
                + " ".join(shlex.quote(test) for test in py_tests)
                + " -v --tb=short"
            )
        if js_tests:
            commands.append(
                "npm test -- " + " ".join(shlex.quote(test) for test in js_tests)
            )
        return commands

    @staticmethod
    def _has_nearby_test(path: str, tests: Iterable[str]) -> bool:
        stem = Path(path).stem.lower().replace("-", "_")
        if stem in {"index", "__init__", ""}:
            return False
        return any(stem in Path(test).stem.lower().replace("-", "_") for test in tests)

    def context(self, query: str, *, limit: int = 8) -> dict[str, Any]:
        hits = self.search(query, limit=limit)
        files = []
        seen = set()
        for hit in hits:
            path = hit["file_path"]
            if path not in seen:
                seen.add(path)
                files.append(path)
        routes = self.routes_for_files(files)
        edges = []
        for path in files[:5]:
            edges.extend(self.callees(path, limit=5))
            edges.extend(self.callers(path, limit=5))
        return {
            "query": query,
            "symbols": hits,
            "files": files,
            "routes": routes,
            "relationships": edges[:20],
        }

    def routes_for_files(self, files: Iterable[str]) -> list[dict[str, Any]]:
        files = list(files)
        if not files:
            return []
        placeholders = ",".join("?" for _ in files)
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    f"SELECT framework, method, route, file_path, line FROM routes WHERE file_path IN ({placeholders}) ORDER BY file_path, line",
                    tuple(files),
                )
            ]

    def _index_file(
        self, conn: sqlite3.Connection, abs_path: Path, rel: str, stat: os.stat_result
    ) -> None:
        self._delete_file(conn, rel)
        conn.execute(
            "INSERT INTO files(path, mtime, size, indexed_at, language, is_test) VALUES(?,?,?,?,?,?)",
            (
                rel,
                stat.st_mtime,
                stat.st_size,
                time.time(),
                abs_path.suffix.lstrip("."),
                int(self._is_test_path(rel)),
            ),
        )
        tags = list(self.repomap.get_tags(str(abs_path), rel) or [])
        for tag in tags:
            if tag.kind == "def":
                conn.execute(
                    "INSERT OR IGNORE INTO symbols(name, kind, file_path, line) VALUES(?,?,?,?)",
                    (tag.name, tag.kind, rel, int(tag.line)),
                )
            elif tag.kind == "ref":
                conn.execute(
                    "INSERT INTO refs(name, file_path, line) VALUES(?,?,?)",
                    (tag.name, rel, int(tag.line)),
                )
        self._index_routes(conn, abs_path, rel)
        self._rebuild_edges(conn)
        self._rebuild_fts(conn)

    def _delete_file(self, conn: sqlite3.Connection, rel: str) -> None:
        conn.execute("DELETE FROM files WHERE path=?", (rel,))
        conn.execute("DELETE FROM symbols WHERE file_path=?", (rel,))
        conn.execute("DELETE FROM refs WHERE file_path=?", (rel,))
        conn.execute("DELETE FROM routes WHERE file_path=?", (rel,))
        conn.execute("DELETE FROM edges WHERE src_file=? OR dst_file=?", (rel, rel))

    def _rebuild_edges(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM edges")
        rows = conn.execute("""
            SELECT r.file_path AS src_file, s.file_path AS dst_file, r.name AS symbol, COUNT(*) AS weight
            FROM refs r JOIN symbols s ON s.name = r.name
            WHERE r.file_path != s.file_path
            GROUP BY r.file_path, s.file_path, r.name
            """).fetchall()
        conn.executemany(
            "INSERT OR IGNORE INTO edges(edge_type, src_file, dst_file, symbol, weight) VALUES('references',?,?,?,?)",
            [
                (r["src_file"], r["dst_file"], r["symbol"], float(r["weight"]))
                for r in rows
            ],
        )

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("DELETE FROM symbol_fts")
        except sqlite3.OperationalError:
            conn.execute("DROP TABLE IF EXISTS symbol_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE symbol_fts USING fts5(name, kind, file_path)"
            )
        conn.execute(
            "INSERT INTO symbol_fts(rowid, name, kind, file_path) "
            "SELECT id, name, kind, file_path FROM symbols"
        )

    def _index_routes(self, conn: sqlite3.Connection, abs_path: Path, rel: str) -> None:
        try:
            text = abs_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        for lineno, line in enumerate(text.splitlines(), start=1):
            for framework, pattern in ROUTE_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                method, route = self._route_match(framework, match)
                conn.execute(
                    "INSERT OR IGNORE INTO routes(framework, method, route, file_path, line) VALUES(?,?,?,?,?)",
                    (framework, method, route, rel, lineno),
                )
        for framework, pattern in FILE_ROUTE_PATTERNS:
            match = pattern.match(rel)
            if not match:
                continue
            route = self._file_route(match.group(1))
            conn.execute(
                "INSERT OR IGNORE INTO routes(framework, method, route, file_path, line) VALUES(?,?,?,?,?)",
                (framework, "GET", route, rel, 1),
            )

    @staticmethod
    def _route_match(framework: str, match: re.Match[str]) -> tuple[str | None, str]:
        if framework == "django":
            return None, "/" + match.group(1).lstrip("/")
        if len(match.groups()) >= 2:
            method = match.group(1).upper() if framework == "nestjs" else match.group(1)
            return method, match.group(2) or "/"
        return None, match.group(1)

    @staticmethod
    def _file_route(route: str) -> str:
        route = route.replace("/index", "").replace("index", "")
        route = re.sub(r"\[(?:\.\.\.)?([^\]]+)\]", r":\1", route)
        route = re.sub(r"\(([^)]+)\)/?", "", route)
        return "/" + route.strip("/")

    def _edge_query(
        self, name_or_file: str, *, incoming: bool, limit: int
    ) -> list[dict[str, Any]]:
        files = self._resolve_target_files(name_or_file)
        with self._connect() as conn:
            if files:
                col = "dst_file" if incoming else "src_file"
                placeholders = ",".join("?" for _ in files)
                rows = conn.execute(
                    f"SELECT edge_type, src_file, dst_file, symbol, weight FROM edges WHERE {col} IN ({placeholders}) ORDER BY weight DESC LIMIT ?",
                    (*files, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT edge_type, src_file, dst_file, symbol, weight FROM edges WHERE symbol=? ORDER BY weight DESC LIMIT ?",
                    (name_or_file, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def _resolve_target_files(self, target: str) -> set[str]:
        target = target.strip()
        if not target:
            return set()
        rel = (
            self._rel(self.repo_path / target)
            if not Path(target).is_absolute()
            else self._rel(Path(target))
        )
        with self._connect() as conn:
            files = {
                r[0]
                for r in conn.execute(
                    "SELECT file_path FROM symbols WHERE name=?", (target,)
                )
            }
            if conn.execute("SELECT 1 FROM files WHERE path=?", (rel,)).fetchone():
                files.add(rel)
        return files

    def _git_files(self) -> list[str]:
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=self.repo_path,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError:
            return []
        return sorted(p for p in result.stdout.splitlines() if p)

    def _git_changed_files(self) -> list[str]:
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.repo_path,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError:
            return []
        return [
            p
            for p in result.stdout.splitlines()
            if p and self._is_code_file(self.repo_path / p)
        ]

    def _rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.repo_path).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _is_code_file(path: Path) -> bool:
        return path.suffix.lower() in CODE_SUFFIXES and not any(
            part in SKIP_DIRS for part in path.parts
        )

    @staticmethod
    def _is_test_path(path: str) -> bool:
        return bool(TEST_PAT.search(path))

    @staticmethod
    def _fts_query(query: str) -> str:
        parts = [p for p in re.split(r"\W+", query) if p]
        return " OR ".join(f'"{p}"' for p in parts) or query

    @staticmethod
    def dumps(data: Any) -> str:
        if hasattr(data, "__dataclass_fields__"):
            data = asdict(data)
        return json.dumps(data, indent=2, sort_keys=True)
