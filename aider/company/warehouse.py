"""Thin warehouse registry for Git-backed Aider Plus product repositories."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WAREHOUSE_REGISTRY = "warehouse.json"
DEFAULT_PRODUCTS_DIRNAME = "products"


class WarehouseError(ValueError):
    """Raised when warehouse operations cannot be completed."""


@dataclass(frozen=True)
class ProductRecord:
    """Registry entry for one Git-backed product repository."""

    name: str
    slug: str
    path: str
    template: str | None = None
    idea: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "path": self.path,
            "template": self.template,
            "idea": self.idea,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify_product_name(name: str) -> str:
    """Return a filesystem-safe product slug."""

    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        raise WarehouseError("Product name must contain at least one letter or number.")
    return slug


def default_warehouse_path(cwd: str | Path | None = None) -> Path:
    """Default to a local ./products warehouse for studio-style product work."""

    root = Path(cwd) if cwd is not None else Path.cwd()
    return root.resolve() / DEFAULT_PRODUCTS_DIRNAME


class WarehouseManager:
    """Manage a registry of normal Git repositories under a products directory."""

    def __init__(self, root: str | Path | None = None):
        self.root = (
            Path(root).expanduser().resolve() if root else default_warehouse_path()
        )
        self.registry_path = self.root / WAREHOUSE_REGISTRY

    def init(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".aider" / "coo").mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._write_registry(
                {"version": 1, "products": {}, "created_at": utc_now()}
            )
        else:
            self._write_registry(self._read_registry())
        return self._read_registry()

    def list_products(self) -> list[ProductRecord]:
        registry = self._read_registry(require_exists=True)
        products = registry.get("products", {})
        return [ProductRecord(**record) for record in products.values()]

    def status(self) -> dict[str, Any]:
        registry = self._read_registry(require_exists=True)
        products = self.list_products()
        existing = [p for p in products if Path(p.path).exists()]
        missing = [p for p in products if not Path(p.path).exists()]
        return {
            "root": str(self.root),
            "registry": str(self.registry_path),
            "products": len(products),
            "existing_products": len(existing),
            "missing_products": len(missing),
            "coo_memory": str(self.root / ".aider" / "coo"),
        }

    def create_product(
        self,
        *,
        name: str,
        idea: str,
        template: str | None = None,
    ) -> ProductRecord:
        self.init()
        slug = slugify_product_name(name)
        product_path = self.root / slug
        product_path.mkdir(parents=True, exist_ok=True)
        self._ensure_git_repo(product_path)

        registry = self._read_registry()
        products = registry.setdefault("products", {})
        now = utc_now()
        existing = products.get(slug, {})
        record = ProductRecord(
            name=name,
            slug=slug,
            path=str(product_path),
            template=template,
            idea=idea,
            created_at=existing.get("created_at") or now,
            updated_at=now,
        )
        products[slug] = record.to_dict()
        registry["updated_at"] = now
        self._write_registry(registry)
        return record

    def get_product(self, name_or_slug: str) -> ProductRecord:
        slug = slugify_product_name(name_or_slug)
        registry = self._read_registry(require_exists=True)
        record = registry.get("products", {}).get(slug)
        if not record:
            raise WarehouseError(f"Unknown warehouse product: {name_or_slug}")
        return ProductRecord(**record)

    def _read_registry(self, *, require_exists: bool = False) -> dict[str, Any]:
        if not self.registry_path.exists():
            if require_exists:
                raise WarehouseError(
                    f"No warehouse registry found at {self.registry_path}. Run `aider warehouse init`."
                )
            return {"version": 1, "products": {}, "created_at": utc_now()}
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WarehouseError(
                f"Invalid warehouse registry: {self.registry_path}"
            ) from exc

    def _write_registry(self, registry: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _ensure_git_repo(path: Path) -> None:
        if (path / ".git").exists():
            return
        try:
            subprocess.run(
                ["git", "init"],
                cwd=path,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise WarehouseError(
                f"Unable to initialize git repo at {path}: {exc}"
            ) from exc
