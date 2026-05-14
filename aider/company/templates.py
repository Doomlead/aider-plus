"""Zero-to-MVP product templates for Aider Plus Company Mode.

The templates are intentionally lightweight. They scaffold a coherent MVP shape
without copying a full framework distribution, then ground Product, UX,
Engineering, QA, and DevOps prompts with product-shape-specific expectations so
the same repo-native Aider workflow can create v0 and keep iterating afterward.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from string import Template


@dataclass(frozen=True)
class ProjectTemplate:
    """A reusable product-shape template for zero-to-MVP creation."""

    key: str
    label: str
    description: str
    discovery_focus: tuple[str, ...]
    engineering_defaults: tuple[str, ...]
    qa_focus: tuple[str, ...]
    iteration_hooks: tuple[str, ...]

    def summary(self) -> str:
        return f"{self.key}: {self.label} — {self.description}"


TEMPLATES: dict[str, ProjectTemplate] = {
    "saas-dashboard": ProjectTemplate(
        key="saas-dashboard",
        label="SaaS dashboard",
        description="Authenticated web app with dashboard metrics, CRUD workflows, and admin views.",
        discovery_focus=(
            "target account/user roles and permission boundaries",
            "core dashboard objects, metrics, and empty states",
            "billing/subscription or trial expectations if in scope",
        ),
        engineering_defaults=(
            "choose a simple app structure before adding dependencies",
            "include authentication/session placeholders when real auth is not configured",
            "keep dashboard data contracts explicit and easy to replace with real backends",
        ),
        qa_focus=(
            "navigation, authentication guards, CRUD happy paths, and empty/error states",
            "unit coverage for data transforms and permission checks",
        ),
        iteration_hooks=(
            "track requested metric/card changes as playbook guidance",
            "preserve extension points for billing, roles, and integrations after v0",
        ),
    ),
    "cli-tool": ProjectTemplate(
        key="cli-tool",
        label="CLI tool",
        description="Command-line product with subcommands, config, help text, and tests.",
        discovery_focus=(
            "primary command verbs and input/output contracts",
            "configuration sources, environment variables, and defaults",
            "failure modes and exit-code expectations",
        ),
        engineering_defaults=(
            "use a small command dispatcher or the repo's existing CLI framework",
            "include --help-friendly names, validation, and deterministic output",
            "avoid network calls in tests unless explicitly requested",
        ),
        qa_focus=(
            "argument parsing, exit codes, invalid inputs, and golden output snapshots",
            "smoke tests for each documented subcommand",
        ),
        iteration_hooks=(
            "capture user feedback about flags and defaults in project memory",
            "keep command handlers small so future subcommands are easy to add",
        ),
    ),
    "fastapi-backend": ProjectTemplate(
        key="fastapi-backend",
        label="FastAPI backend",
        description="Python API service with routes, schemas, persistence boundaries, and tests.",
        discovery_focus=(
            "API consumers, resource model, and authentication expectations",
            "persistence choice and migration expectations",
            "OpenAPI/schema compatibility requirements",
        ),
        engineering_defaults=(
            "separate routers, schemas, service logic, and persistence adapters",
            "include health check and clear error response shapes",
            "prefer dependency-injected stores so tests can run without external services",
        ),
        qa_focus=(
            "route tests for success, validation errors, auth boundaries, and not-found cases",
            "schema assertions for request/response contracts",
        ),
        iteration_hooks=(
            "record API contract changes and backwards-compatibility notes",
            "keep persistence adapters swappable for later database integration",
        ),
    ),
    "nextjs-app": ProjectTemplate(
        key="nextjs-app",
        label="Next.js app",
        description="Modern React/Next.js product with routes, components, state, and UI tests.",
        discovery_focus=(
            "primary pages/routes and conversion or engagement goals",
            "component states, responsive behavior, and accessibility needs",
            "data-fetching model and deployment target",
        ),
        engineering_defaults=(
            "model routes/components before styling detail",
            "keep data-loading boundaries explicit and mockable",
            "include accessible labels, keyboard states, and loading/error states",
        ),
        qa_focus=(
            "component rendering, route smoke tests, accessibility-critical states",
            "data fetching and empty/error/loading states",
        ),
        iteration_hooks=(
            "store UX preferences from approvals and QA in the playbook",
            "leave clear seams for analytics, auth, and backend integration after v0",
        ),
    ),
    "discord-bot": ProjectTemplate(
        key="discord-bot",
        label="Discord bot",
        description="Bot with commands, event handling, permissions, and operational safeguards.",
        discovery_focus=(
            "guild/user roles, command list, and moderation boundaries",
            "event triggers, rate limits, and escalation behavior",
            "token/config handling and deployment surface",
        ),
        engineering_defaults=(
            "keep command handlers isolated from Discord transport where possible",
            "never hard-code tokens; document environment variables",
            "include safe permission failures and audit-friendly logging",
        ),
        qa_focus=(
            "command parsing, permission denial, event payload handling, and config validation",
            "unit tests with mocked Discord clients/events",
        ),
        iteration_hooks=(
            "record user command wording changes and moderation edge cases",
            "keep transport adapters thin for future Slack/CLI reuse",
        ),
    ),
    "browser-extension": ProjectTemplate(
        key="browser-extension",
        label="Browser extension",
        description="Extension with manifest, popup/options UI, content scripts, and permissions.",
        discovery_focus=(
            "target browser, pages/domains, and required permissions",
            "popup/options/content-script responsibilities",
            "privacy constraints and data retention expectations",
        ),
        engineering_defaults=(
            "minimize manifest permissions and document why each permission exists",
            "separate content-script DOM code from business logic",
            "include graceful behavior when pages do not match expected DOM shapes",
        ),
        qa_focus=(
            "manifest validity, permission scope, content-script parsing, and UI states",
            "unit tests for pure logic separated from browser APIs",
        ),
        iteration_hooks=(
            "track permission/privacy review notes as release gates",
            "keep selectors configurable where target pages change often",
        ),
    ),
    "data-app": ProjectTemplate(
        key="data-app",
        label="Data app",
        description="Interactive data workflow with ingestion, transforms, charts, and exports.",
        discovery_focus=(
            "data sources, freshness, volume, and sensitive fields",
            "core questions the app must answer and chart types needed",
            "export/sharing requirements and refresh cadence",
        ),
        engineering_defaults=(
            "separate ingestion, transformation, and presentation layers",
            "include sample data or fixtures when real data is unavailable",
            "make chart/data contracts explicit and testable",
        ),
        qa_focus=(
            "transform correctness, missing/dirty data, chart contract snapshots, and export paths",
            "tests using small representative fixtures",
        ),
        iteration_hooks=(
            "capture recurring data-quality issues in the playbook",
            "leave room for new sources and metrics without rewriting charts",
        ),
    ),
    "internal-admin": ProjectTemplate(
        key="internal-admin",
        label="Internal admin tool",
        description="Back-office UI for operations teams with roles, workflows, and auditability.",
        discovery_focus=(
            "operator roles, approval boundaries, and audit requirements",
            "key entities, queues, filters, and bulk actions",
            "risk level for destructive actions and rollback expectations",
        ),
        engineering_defaults=(
            "make permissions and destructive actions explicit in code and UI copy",
            "include audit-log hooks or placeholders for sensitive operations",
            "prefer safe defaults and confirmation states",
        ),
        qa_focus=(
            "permission boundaries, destructive-action confirmations, filters, and bulk operations",
            "audit event assertions for sensitive workflows",
        ),
        iteration_hooks=(
            "learn operational exceptions and approval preferences from each run",
            "keep workflow queues extensible for future departments or statuses",
        ),
    ),
}

COMMON_STARTER_FILES: dict[str, str] = {
    ".gitignore": """# Aider Plus product repo
.env
.env.*
!.env.example
__pycache__/
.pytest_cache/
node_modules/
dist/
build/
.next/
.DS_Store
""",
    "docs/product-brief.md": """# $project_name Product Brief

## Idea
$idea

## Template
$template_label (`$template_key`)

## Delivery Loop
- Product: clarify the core user, promise, and MVP scope.
- UX: define the first-run flow, main screens, and accessible states.
- Engineering: keep the implementation repo-native, modular, and testable.
- QA: add smoke checks around the critical path and edge states.
- DevOps: document local run, configuration, and deployment assumptions.

## Next Iteration Notes
Capture user approvals, QA findings, and follow-up scope here so future company
runs can keep evolving the same Git repository.
""",
}

TEMPLATE_STARTER_FILES: dict[str, dict[str, str]] = {
    "saas-dashboard": {
        "README.md": """# $project_name

$idea

Aider Plus scaffolded this as a SaaS dashboard MVP. Start with the product brief
in `docs/product-brief.md`, then evolve the app with Company Mode inside this
normal Git repository.

## Suggested MVP Structure
- `src/app/` — pages, routes, and dashboard shell
- `src/components/` — reusable UI components
- `src/lib/` — data contracts, permissions, and service adapters
- `tests/` — smoke and unit checks for critical flows

## Local Development
Document the selected framework commands here after the first implementation
pass, for example install, test, lint, and run commands.
""",
        "src/app/README.md": """# App shell

Define routes, layouts, dashboard states, and authenticated/unauthenticated
boundaries here.
""",
        "src/components/README.md": """# Components

Keep metric cards, tables, forms, empty states, and dialogs reusable and
accessible.
""",
        "src/lib/README.md": """# Product logic

Keep data contracts, permission helpers, and mock service adapters here so the
MVP can later connect to real auth, billing, and persistence.
""",
        "tests/README.md": """# Tests

Add smoke tests for navigation, auth guards, CRUD paths, and dashboard empty or
error states.
""",
    },
    "nextjs-app": {
        "README.md": """# $project_name

$idea

Aider Plus scaffolded this as a Next.js-style MVP workspace while keeping the
repo Git-native and dependency-light until implementation choices are confirmed.

## Suggested MVP Structure
- `app/` — routes and page-level data boundaries
- `components/` — accessible UI components and states
- `lib/` — product data, service adapters, and pure helpers
- `tests/` — route/component smoke tests

## Local Development
After Company Mode selects concrete dependencies, document install, run, lint,
and test commands here.
""",
        "app/README.md": """# Routes

Model primary routes, loading/error/empty states, and conversion or engagement
flows here.
""",
        "components/README.md": """# Components

Build accessible components with explicit props for loading, disabled, error,
and responsive states.
""",
        "lib/README.md": """# Product data and helpers

Keep fetch boundaries, mocked data, and reusable transformations isolated from
UI components.
""",
        "tests/README.md": """# Tests

Add component rendering, route smoke, accessibility-critical, and data state
checks here.
""",
    },
    "fastapi-backend": {
        "README.md": """# $project_name

$idea

Aider Plus scaffolded this as a FastAPI backend MVP. Keep route contracts,
service logic, and persistence boundaries separate as the API evolves.

## Suggested MVP Structure
- `app/main.py` — API entrypoint and health route
- `app/routers/` — resource routes
- `app/schemas/` — request/response contracts
- `app/services/` — business logic
- `app/repositories/` — persistence adapters
- `tests/` — route and schema tests
""",
        "app/main.py": """\"\"\"$project_name API entrypoint.\"\"\"\n\n\ndef health() -> dict[str, str]:\n    return {\"status\": \"ok\", \"product\": \"$project_slug\"}\n""",
        "app/routers/README.md": "Route modules live here. Keep transport concerns thin.\n",
        "app/schemas/README.md": "Request and response models live here.\n",
        "app/services/README.md": "Business logic lives here, outside route handlers.\n",
        "app/repositories/README.md": "Persistence adapters live here and should be swappable in tests.\n",
        "tests/README.md": "Add route, validation, auth boundary, and not-found tests here.\n",
    },
    "cli-tool": {
        "README.md": """# $project_name

$idea

Aider Plus scaffolded this as a CLI MVP.

## Suggested MVP Structure
- `src/$python_package/cli.py` — argument parsing and command dispatch
- `src/$python_package/commands/` — small command handlers
- `tests/` — parser, exit-code, and golden-output checks
""",
        "src/$python_package/__init__.py": '"""$project_name package."""\n',
        "src/$python_package/cli.py": """\"\"\"CLI entrypoint for $project_name.\"\"\"\n\n\ndef main(argv: list[str] | None = None) -> int:\n    \"\"\"Run the CLI. Replace this placeholder during implementation.\"\"\"\n    return 0\n""",
        "src/$python_package/commands/README.md": "Keep command handlers small and testable.\n",
        "tests/README.md": "Add argument parsing, exit code, invalid input, and golden-output tests here.\n",
    },
    "discord-bot": {
        "README.md": """# $project_name

$idea

Aider Plus scaffolded this as a Discord bot MVP.

## Suggested MVP Structure
- `bot/commands/` — command handlers
- `bot/events/` — event handlers
- `bot/services/` — moderation/business logic
- `tests/` — mocked Discord client and permission checks

Never commit Discord tokens. Document required environment variables in
`.env.example` before running the bot.
""",
        ".env.example": "DISCORD_TOKEN=\nDISCORD_GUILD_ID=\n",
        "bot/commands/README.md": "Command handlers live here. Keep permission checks explicit.\n",
        "bot/events/README.md": "Event handlers live here. Avoid business logic in transport code.\n",
        "bot/services/README.md": "Reusable moderation or product services live here.\n",
        "tests/README.md": "Add mocked command, permission-denial, event payload, and config tests here.\n",
    },
    "browser-extension": {
        "README.md": """# $project_name

$idea

Aider Plus scaffolded this as a browser extension MVP.

## Suggested MVP Structure
- `extension/manifest.json` — minimal permissions and extension metadata
- `extension/popup/` — popup UI
- `extension/options/` — options UI
- `extension/content/` — content scripts
- `src/` — reusable logic separated from browser APIs
- `tests/` — manifest, selector, and pure-logic checks
""",
        "extension/manifest.json": """{
  "manifest_version": 3,
  "name": "$project_name",
  "version": "0.0.1",
  "permissions": []
}
""",
        "extension/popup/README.md": "Popup UI files live here.\n",
        "extension/options/README.md": "Options UI and settings flows live here.\n",
        "extension/content/README.md": "Content scripts live here. Keep DOM selectors configurable.\n",
        "src/README.md": "Reusable extension logic separated from browser APIs lives here.\n",
        "tests/README.md": "Add manifest, permission, selector, and pure-logic tests here.\n",
    },
    "data-app": {
        "README.md": """# $project_name

$idea

Aider Plus scaffolded this as a data app MVP.

## Suggested MVP Structure
- `data/sample/` — representative fixtures
- `src/ingestion/` — source readers and refresh boundaries
- `src/transforms/` — pure transformations and metrics
- `src/presentation/` — charts, views, and export contracts
- `tests/` — fixture-driven transform and chart contract tests
""",
        "data/sample/README.md": "Place small representative sample data fixtures here.\n",
        "src/ingestion/README.md": "Source readers and freshness checks live here.\n",
        "src/transforms/README.md": "Pure transforms and metric definitions live here.\n",
        "src/presentation/README.md": "Chart contracts, views, and export adapters live here.\n",
        "tests/README.md": "Add fixture-driven transform, dirty data, and export contract tests here.\n",
    },
    "internal-admin": {
        "README.md": """# $project_name

$idea

Aider Plus scaffolded this as an internal admin MVP.

## Suggested MVP Structure
- `src/app/` — queues, detail screens, and workflows
- `src/components/` — confirmation states, filters, tables, and forms
- `src/lib/permissions/` — role and approval boundaries
- `src/lib/audit/` — audit event contracts or placeholders
- `tests/` — permission, destructive-action, and filter checks
""",
        "src/app/README.md": "Workflow queues, details, and operator flows live here.\n",
        "src/components/README.md": "Admin components, confirmation states, filters, and tables live here.\n",
        "src/lib/permissions/README.md": "Role checks and approval boundaries live here.\n",
        "src/lib/audit/README.md": "Audit event contracts and placeholders live here.\n",
        "tests/README.md": "Add permission, destructive-action, filter, and audit assertion tests here.\n",
    },
}


DEFAULT_TEMPLATE_KEY = "saas-dashboard"


def list_templates() -> list[ProjectTemplate]:
    """Return templates in display order."""

    return [TEMPLATES[key] for key in sorted(TEMPLATES)]


def get_template(key: str | None) -> ProjectTemplate:
    """Return a template by key, raising ValueError for unknown keys."""

    resolved = (key or DEFAULT_TEMPLATE_KEY).strip().lower()
    try:
        return TEMPLATES[resolved]
    except KeyError as exc:
        choices = ", ".join(sorted(TEMPLATES))
        raise ValueError(
            f"Unknown project template '{key}'. Available templates: {choices}"
        ) from exc


def render_template_starter_files(
    *,
    idea: str,
    template_key: str | None = None,
    project_name: str,
    project_slug: str,
) -> dict[str, str]:
    """Return the starter file map for a product template.

    The files are intentionally thin placeholders and docs, not a full generated
    framework. They give Company Mode a coherent MVP structure to iterate inside
    while preserving the repo as a normal Git repository.
    """

    template = get_template(template_key)
    python_package = project_slug.replace("-", "_")
    values = {
        "idea": idea.strip(),
        "project_name": project_name.strip(),
        "project_slug": project_slug,
        "python_package": python_package,
        "template_key": template.key,
        "template_label": template.label,
    }
    files: dict[str, str] = {
        ".aider/company/product.json": json.dumps(
            {
                "name": project_name.strip(),
                "slug": project_slug,
                "template": template.key,
                "idea": idea.strip(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    }
    for path, content in {
        **COMMON_STARTER_FILES,
        **TEMPLATE_STARTER_FILES.get(template.key, {}),
    }.items():
        rendered_path = Template(path).safe_substitute(values)
        files[rendered_path] = Template(content).safe_substitute(values)
    return files


def render_zero_to_mvp_prompt(
    *,
    idea: str,
    template_key: str | None = None,
    project_name: str | None = None,
) -> str:
    """Render the canonical zero-to-MVP prompt used by `aider company create`."""

    template = get_template(template_key)
    name_line = (
        f"Project name: {project_name.strip()}"
        if project_name
        else "Project name: infer a concise name from the idea"
    )

    def bullets(items: tuple[str, ...]) -> str:
        return "\n".join(f"- {item}" for item in items)

    return "\n".join(
        [
            "You are Aider Plus Company Mode creating a zero-to-MVP product.",
            "",
            "Mission:",
            idea.strip(),
            "",
            name_line,
            f"Product template: {template.label} ({template.key})",
            f"Template description: {template.description}",
            "",
            "Use the Aider Plus delivery loop: Product -> UX -> Engineering -> QA -> DevOps.",
            "The goal is not a one-shot code dump. Create v0 in a way that can keep evolving in",
            "this git repository after the first implementation.",
            "",
            "Product discovery focus:",
            bullets(template.discovery_focus),
            "",
            "Engineering defaults:",
            bullets(template.engineering_defaults),
            "",
            "QA focus:",
            bullets(template.qa_focus),
            "",
            "Iteration and memory hooks:",
            bullets(template.iteration_hooks),
            "",
            "Required output and behavior:",
            "- Inspect the repository before choosing files or frameworks.",
            "- If the repo is empty, create the smallest coherent MVP structure for this template.",
            "- If the repo already has an app/framework, extend it instead of replacing it.",
            "- Produce or update docs that explain how to run the MVP and how to iterate on it.",
            "- Add tests or smoke checks where practical, and state any checks that cannot run.",
            "- Keep changes reviewable with small cohesive files and clear boundaries.",
            "- Prefer explicit TODOs for integrations that need secrets or external accounts.",
            "- Summarize the Product, UX, Engineering, QA, release, and post-mortem outcomes.",
        ]
    )
