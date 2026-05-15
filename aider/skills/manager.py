from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from aider.memory.retrieval import MemoryRetriever

SKILL_FILE = "SKILL.md"
MAX_SKILLS_PER_SCOPE = 100
DEFAULT_QUERY_K = 3
_ALLOWED_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ALLOWED_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")


@dataclass
class SkillSummary:
    name: str
    scope: str
    title: str
    description: str
    path: str
    metadata: dict = field(default_factory=dict)

    def injectable_text(self) -> str:
        prefix = f"{self.scope}/{self.name}"
        body = self.description or self.title
        return f"{prefix}: {body}" if body else prefix


@dataclass
class SkillDocument(SkillSummary):
    content: str = ""
    support_files: list[str] = field(default_factory=list)


class SkillManager:
    """Filesystem-backed procedural-memory skill store.

    Skills are intentionally plain directories with a required SKILL.md file and
    optional references/, templates/, scripts/, and assets/ folders. Mutating
    operations validate paths and cap growth so learned workflows remain local,
    inspectable, and bounded.
    """

    def __init__(self, root: str | Path, *, max_skills_per_scope: int = MAX_SKILLS_PER_SCOPE):
        self.root = Path(root).resolve()
        self.max_skills_per_scope = max_skills_per_scope

    def list_skills(self, scopes: Optional[Iterable[str]] = None) -> list[SkillSummary]:
        summaries: list[SkillSummary] = []
        target_scopes = list(scopes or self._discover_scopes())
        for scope in target_scopes:
            scope_dir = self._scope_dir(scope, create=False)
            if not scope_dir.exists():
                continue
            for skill_dir in sorted(p for p in scope_dir.iterdir() if p.is_dir()):
                skill_file = skill_dir / SKILL_FILE
                if not skill_file.exists():
                    continue
                summaries.append(self._summary_from_file(scope, skill_dir.name, skill_file))
        return summaries

    def query_skills(
        self,
        query: str,
        *,
        scopes: Iterable[str],
        k: int = DEFAULT_QUERY_K,
        min_score: float = 0.05,
    ) -> list[SkillSummary]:
        skills = self.list_skills(scopes)
        if not skills:
            return []
        if len(skills) <= k:
            return skills
        texts = [skill.injectable_text() for skill in skills]
        top = MemoryRetriever(texts).top_k(query, k=k, min_score=min_score)
        by_text = {text: skill for text, skill in zip(texts, skills)}
        return [by_text[text] for text, _score in top if text in by_text]

    def read_skill(self, scope: str, name: str) -> SkillDocument:
        skill_dir = self._skill_dir(scope, name, create=False)
        skill_file = skill_dir / SKILL_FILE
        if not skill_file.exists():
            raise FileNotFoundError(f"No skill found at {skill_file}")
        summary = self._summary_from_file(scope, name, skill_file)
        support_files = [
            str(path.relative_to(skill_dir))
            for path in sorted(skill_dir.rglob("*"))
            if path.is_file() and path.name != SKILL_FILE
        ]
        return SkillDocument(
            **asdict(summary),
            content=skill_file.read_text(encoding="utf-8"),
            support_files=support_files,
        )

    def create_skill(
        self,
        *,
        scope: str,
        name: str,
        content: str,
        metadata: Optional[dict] = None,
        files: Optional[dict[str, str]] = None,
    ) -> SkillDocument:
        scope_dir = self._scope_dir(scope, create=True)
        existing = [p for p in scope_dir.iterdir() if p.is_dir()]
        if len(existing) >= self.max_skills_per_scope and not (scope_dir / name).exists():
            raise ValueError(f"Skill scope '{scope}' has reached the configured cap")
        skill_dir = self._skill_dir(scope, name, create=True)
        skill_file = skill_dir / SKILL_FILE
        if skill_file.exists():
            raise FileExistsError(f"Skill already exists: {scope}/{name}")
        skill_file.write_text(self._with_metadata(content, metadata), encoding="utf-8")
        for rel_path, file_content in (files or {}).items():
            self.write_skill_file(scope, name, rel_path, file_content)
        return self.read_skill(scope, name)

    def patch_skill(self, *, scope: str, name: str, old: str, new: str) -> SkillDocument:
        if not old:
            raise ValueError("patch_skill requires non-empty old text")
        doc = self.read_skill(scope, name)
        if old not in doc.content:
            raise ValueError("Patch target text was not found in skill")
        path = self._skill_dir(scope, name, create=False) / SKILL_FILE
        path.write_text(doc.content.replace(old, new, 1), encoding="utf-8")
        return self.read_skill(scope, name)

    def write_skill_file(self, scope: str, name: str, relative_path: str, content: str) -> Path:
        skill_dir = self._skill_dir(scope, name, create=True)
        target = self._safe_child(skill_dir, relative_path)
        if target.name == SKILL_FILE:
            raise ValueError("Use create_skill/patch_skill for SKILL.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _discover_scopes(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            p.name
            for p in self.root.iterdir()
            if p.is_dir() and _ALLOWED_SCOPE_RE.match(p.name)
        )

    def _scope_dir(self, scope: str, *, create: bool) -> Path:
        if not _ALLOWED_SCOPE_RE.match(scope):
            raise ValueError(f"Invalid skill scope: {scope!r}")
        path = self.root / scope
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def _skill_dir(self, scope: str, name: str, *, create: bool) -> Path:
        if not _ALLOWED_NAME_RE.match(name):
            raise ValueError(f"Invalid skill name: {name!r}")
        path = self._scope_dir(scope, create=create) / name
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def _safe_child(self, base: Path, relative_path: str) -> Path:
        if Path(relative_path).is_absolute():
            raise ValueError("Skill support paths must be relative")
        target = (base / relative_path).resolve()
        if base.resolve() not in target.parents and target != base.resolve():
            raise ValueError("Skill support path escapes the skill directory")
        return target

    def _summary_from_file(self, scope: str, name: str, skill_file: Path) -> SkillSummary:
        content = skill_file.read_text(encoding="utf-8")
        title = self._extract_title(content) or name.replace("-", " ").title()
        description = self._extract_description(content)
        metadata = self._extract_metadata(content)
        return SkillSummary(
            name=name,
            scope=scope,
            title=title,
            description=description,
            path=str(skill_file),
            metadata=metadata,
        )

    @staticmethod
    def _extract_title(content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return ""

    @staticmethod
    def _extract_description(content: str) -> str:
        lines = content.splitlines()
        for idx, line in enumerate(lines):
            if line.strip().lower().startswith("description:"):
                return line.split(":", 1)[1].strip()
            if line.strip().startswith("# "):
                following = [x.strip() for x in lines[idx + 1 : idx + 6] if x.strip()]
                return following[0] if following else ""
        return ""

    @staticmethod
    def _extract_metadata(content: str) -> dict:
        marker = "<!-- aider-plus-skill:"
        end_marker = "-->"
        if marker not in content:
            return {}
        start = content.find(marker) + len(marker)
        end = content.find(end_marker, start)
        if end < 0:
            return {}
        raw = content[start:end].strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _with_metadata(content: str, metadata: Optional[dict]) -> str:
        payload = dict(metadata or {})
        payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        payload.setdefault("approval_status", "approved")
        block = "<!-- aider-plus-skill: " + json.dumps(payload, sort_keys=True) + " -->"
        text = content.strip() + "\n"
        return text if "<!-- aider-plus-skill:" in text else f"{block}\n{text}"
