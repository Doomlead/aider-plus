"""Thin warehouse registry for Git-backed Aider Plus product repositories."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aider.company.templates import get_template, render_template_starter_files

WAREHOUSE_REGISTRY = "warehouse.json"
DEFAULT_WAREHOUSE_DIRNAME = "AiderPlusWarehouse"
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
    """Default to a local ./AiderPlusWarehouse studio root."""

    root = Path(cwd) if cwd is not None else Path.cwd()
    return root.resolve() / DEFAULT_WAREHOUSE_DIRNAME


class WarehouseManager:
    """Manage a registry of normal Git repositories under a products directory."""

    def __init__(self, root: str | Path | None = None):
        self.root = (
            Path(root).expanduser().resolve() if root else default_warehouse_path()
        )
        self.registry_path = self.root / WAREHOUSE_REGISTRY

    @property
    def products_dir(self) -> Path:
        """Directory that contains Git-backed product repositories."""

        return self.root / DEFAULT_PRODUCTS_DIRNAME

    def init(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        self.products_dir.mkdir(parents=True, exist_ok=True)
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
        return self._records_from_registry(registry)

    def status(self) -> dict[str, Any]:
        registry = self._read_registry(require_exists=True)
        products = self._records_from_registry(registry)
        existing_count = sum(1 for product in products if Path(product.path).exists())
        missing_count = len(products) - existing_count
        return {
            "root": str(self.root),
            "registry": str(self.registry_path),
            "products": len(products),
            "existing_products": existing_count,
            "missing_products": missing_count,
            "products_dir": str(self.products_dir),
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
        product_path = self.products_dir / slug
        product_path.mkdir(parents=True, exist_ok=True)
        self._ensure_git_repo(product_path)
        resolved_template = get_template(template)
        self._write_starter_files(
            product_path=product_path,
            name=name,
            slug=slug,
            idea=idea,
            template=resolved_template.key,
        )
        self._apply_post_creation_hooks(
            product_path=product_path,
            template=resolved_template.key,
        )
        self._commit_initial_scaffold(
            product_path=product_path,
            template=resolved_template.key,
        )

        registry = self._read_registry()
        products = registry.setdefault("products", {})
        now = utc_now()
        existing = products.get(slug, {})
        record = ProductRecord(
            name=name,
            slug=slug,
            path=str(product_path),
            template=resolved_template.key,
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
        record = self._product_entries(registry).get(slug)
        if not isinstance(record, dict):
            raise WarehouseError(f"Unknown warehouse product: {name_or_slug}")
        return self._record_from_dict(record)

    @staticmethod
    def _records_from_registry(registry: dict[str, Any]) -> list[ProductRecord]:
        return [
            WarehouseManager._record_from_dict(record)
            for record in WarehouseManager._product_entries(registry).values()
            if isinstance(record, dict)
        ]

    @staticmethod
    def _product_entries(registry: dict[str, Any]) -> dict[str, Any]:
        products = registry.get("products", {})
        if not isinstance(products, dict):
            raise WarehouseError(
                "Invalid warehouse registry: products must be an object"
            )
        return products

    @staticmethod
    def _record_from_dict(record: dict[str, Any]) -> ProductRecord:
        return ProductRecord(
            name=str(record.get("name", "")),
            slug=str(record.get("slug", "")),
            path=str(record.get("path", "")),
            template=(
                get_template(str(record["template"])).key
                if record.get("template") is not None
                else None
            ),
            idea=(str(record["idea"]) if record.get("idea") is not None else None),
            created_at=(
                str(record["created_at"])
                if record.get("created_at") is not None
                else None
            ),
            updated_at=(
                str(record["updated_at"])
                if record.get("updated_at") is not None
                else None
            ),
        )

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
    def _write_starter_files(
        *,
        product_path: Path,
        name: str,
        slug: str,
        idea: str,
        template: str | None,
    ) -> None:
        files = render_template_starter_files(
            idea=idea,
            template_key=template,
            project_name=name,
            project_slug=slug,
        )
        for relative_path, content in files.items():
            target = product_path / relative_path
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    @staticmethod
    def _apply_post_creation_hooks(*, product_path: Path, template: str) -> None:
        """Materialize template post-creation guidance inside the product repo."""

        project_template = get_template(template)
        instructions = project_template.post_creation_steps() or [
            "Run Product discovery before adding major dependencies.",
        ]
        target = product_path / ".aider" / "company" / "post-creation.md"
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        bullets = "\n".join(f"- {instruction}" for instruction in instructions)
        target.write_text(
            f"# Post-Creation Hooks: {project_template.label}\n\n"
            "Apply these hooks before the first full Company implementation run.\n\n"
            f"{bullets}\n",
            encoding="utf-8",
        )


    @staticmethod
    def _commit_initial_scaffold(*, product_path: Path, template: str) -> None:
        """Commit newly scaffolded files so product repos start review-ready."""

        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=product_path,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if not status.stdout.strip():
                return
            subprocess.run(
                ["git", "add", "--all"],
                cwd=product_path,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            existing_commit = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=product_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            message = (
                f"Scaffold {template} product"
                if existing_commit.returncode != 0
                else f"Update {template} scaffold"
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Aider Plus",
                    "-c",
                    "user.email=aider-plus@example.invalid",
                    "commit",
                    "-m",
                    message,
                ],
                cwd=product_path,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise WarehouseError(
                f"Unable to commit initial scaffold at {product_path}: {exc}"
            ) from exc

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
