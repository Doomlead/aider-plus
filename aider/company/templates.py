"""Zero-to-MVP product templates for Aider Plus Company Mode.

The templates are intentionally lightweight. They scaffold a coherent MVP shape
without copying a full framework distribution, then ground Product, UX,
Engineering, QA, and DevOps prompts with product-shape-specific expectations so
the same repo-native Aider workflow can create v0 and keep iterating afterward.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from string import Template
from types import MappingProxyType
from typing import Mapping


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
    recommended_skills: list[str] = field(default_factory=list)
    starter_files: dict[str, str] = field(default_factory=dict)
    post_creation_instructions: str = ""
    example_prd_prompt: str = ""
    qa_gates: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Normalize older tuple-based declarations into rich template metadata."""

        object.__setattr__(self, "recommended_skills", list(self.recommended_skills))
        object.__setattr__(self, "starter_files", dict(self.starter_files))
        object.__setattr__(self, "qa_gates", list(self.qa_gates))
        if isinstance(self.post_creation_instructions, (tuple, list)):
            object.__setattr__(
                self,
                "post_creation_instructions",
                "\n".join(str(item) for item in self.post_creation_instructions),
            )

    def summary(self) -> str:
        skills = (
            ", ".join(self.recommended_skills)
            if self.recommended_skills
            else "generalist"
        )
        return f"{self.key}: {self.label} — {self.description} (skills: {skills})"

    def post_creation_steps(self) -> list[str]:
        """Return normalized post-create instructions as display-ready steps."""

        return [
            line.strip("- ")
            for line in self.post_creation_instructions.splitlines()
            if line.strip()
        ]

    def all_qa_gates(self) -> list[str]:
        """Return explicit QA gates, falling back to the older QA focus metadata."""

        return self.qa_gates or list(self.qa_focus)

    def all_starter_files(self) -> Mapping[str, str]:
        """Return immutable template-owned starter files."""

        return MappingProxyType(dict(self.starter_files))


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
    "nextjs-saas": ProjectTemplate(
        key="nextjs-saas",
        label="Next.js SaaS",
        description="Production-shaped SaaS app with onboarding, billing seams, settings, and dashboard flows.",
        discovery_focus=(
            "ideal customer profile, workspace model, and onboarding activation moment",
            "subscription tiers, billing events, and upgrade/downgrade seams",
            "core dashboard jobs-to-be-done, empty states, and collaboration roles",
        ),
        engineering_defaults=(
            "organize app-router routes around marketing, onboarding, app, and settings areas",
            "keep auth, billing, analytics, and data providers behind replaceable adapters",
            "document environment variables and mock external services by default",
        ),
        qa_focus=(
            "onboarding, protected routes, workspace switching, billing placeholders, and empty states",
            "component contracts for loading/error states and accessibility-critical controls",
        ),
        iteration_hooks=(
            "capture pricing, onboarding, and role changes as Product memory before implementation",
            "preserve provider interfaces so Delivery can swap mocks for production services",
        ),
        recommended_skills=("product", "ux", "frontend", "qa", "devops"),
        starter_files={
            "README.md": """# $project_name\n\n$idea\n\nAider Plus scaffolded this as a Next.js SaaS MVP with clear seams for Product, UX, Engineering, Delivery, and DevOps.\n\n## Company Mode\n- Begin in `docs/product-brief.md` and `.aider/company/product.json`.\n- Use `docs/company-mode.md` for department handoffs and acceptance gates.\n- Keep auth, billing, analytics, and data providers mocked until credentials exist.\n\n## Suggested Structure\n- `app/(marketing)/` — public landing and conversion routes\n- `app/(onboarding)/` — activation flow and first workspace setup\n- `app/(app)/dashboard/` — authenticated product experience\n- `components/` — accessible, reusable UI states\n- `lib/providers/` — auth, billing, analytics, and data adapters\n- `tests/` — route, component, and provider-contract checks\n""",
            "app/(marketing)/README.md": "Public landing, pricing, and conversion routes live here.\n",
            "app/(onboarding)/README.md": "First-run activation and workspace setup flows live here.\n",
            "app/(app)/dashboard/README.md": "Authenticated dashboard and core SaaS workflows live here.\n",
            "components/README.md": "Reusable components with loading, empty, error, and disabled states live here.\n",
            "lib/providers/README.md": "Mockable auth, billing, analytics, and data provider adapters live here.\n",
            "tests/README.md": "Add onboarding, protected-route, billing-placeholder, and accessibility checks here.\n",
        },
        post_creation_instructions=(
            "Run Product discovery before selecting auth or billing vendors.",
            "Keep SaaS provider adapters mocked until real credentials and deployment targets are known.",
            "Ask UX to review onboarding, empty states, and upgrade prompts before Engineering hardens flows.",
        ),
        qa_gates=[
            "Product brief names ICP, activation event, roles, and MVP acceptance criteria.",
            "UX covers onboarding, dashboard empty/loading/error states, and upgrade prompts.",
            "Engineering keeps auth, billing, analytics, and data providers behind mockable adapters.",
            "QA verifies protected routes, workspace switching, billing seams, and accessibility basics.",
        ],
        example_prd_prompt="Draft a PRD for a B2B SaaS MVP with onboarding, a workspace dashboard, role-aware settings, and mocked billing seams.",
    ),
    "python-fastapi-api": ProjectTemplate(
        key="python-fastapi-api",
        label="Python FastAPI API",
        description="Contract-first Python API with routers, schemas, services, persistence adapters, and operational checks.",
        discovery_focus=(
            "API consumers, resource lifecycle, idempotency, and auth expectations",
            "data model, persistence boundaries, migration needs, and fixture strategy",
            "SLO, observability, rate-limit, and deployment assumptions for the MVP",
        ),
        engineering_defaults=(
            "separate app factory, routers, Pydantic schemas, services, and repositories",
            "include health/readiness checks plus dependency-injected test stores",
            "write contract-shaped tests before wiring external databases or queues",
        ),
        qa_focus=(
            "OpenAPI shape, validation errors, auth boundaries, idempotency, and not-found paths",
            "repository/service tests using in-memory fixtures and deterministic clocks",
        ),
        iteration_hooks=(
            "record endpoint compatibility notes and migration decisions in product memory",
            "keep infra assumptions explicit so DevOps can add containers and deployment later",
        ),
        recommended_skills=("product", "backend", "qa", "devops"),
        starter_files={
            "README.md": """# $project_name\n\n$idea\n\nAider Plus scaffolded this as a contract-first FastAPI API MVP.\n\n## Company Mode\n- Product owns API consumers, resources, and acceptance criteria.\n- Engineering keeps transport, schemas, services, and persistence adapters separate.\n- QA verifies OpenAPI contracts, validation, and auth/error boundaries.\n- DevOps documents local run, configuration, health checks, and deployment assumptions.\n\n## Suggested Structure\n- `app/main.py` — application factory and health/readiness routes\n- `app/api/routes/` — thin HTTP routers\n- `app/schemas/` — request/response contracts\n- `app/services/` — business logic\n- `app/repositories/` — persistence adapters\n- `tests/` — route, schema, service, and repository tests\n""",
            "app/__init__.py": '"""$project_name API package."""\n',
            "app/main.py": """\"\"\"Application entrypoint for $project_name.\"\"\"\n\n\ndef health() -> dict[str, str]:\n    \"\"\"Return a framework-neutral health payload until FastAPI is installed.\"\"\"\n    return {\"status\": \"ok\", \"product\": \"$project_slug\"}\n""",
            "app/api/routes/README.md": "HTTP route modules live here; keep handlers thin.\n",
            "app/schemas/README.md": "Pydantic request and response contracts live here.\n",
            "app/services/README.md": "Business logic and orchestration live here.\n",
            "app/repositories/README.md": "Persistence adapters live here and should be replaceable in tests.\n",
            "tests/README.md": "Add route, schema, service, auth-boundary, and error-response tests here.\n",
        },
        post_creation_instructions=(
            "Confirm API consumers and auth mode before adding production dependencies.",
            "Keep a health/readiness path and deterministic test fixtures in the first implementation.",
            "Document any endpoint compatibility promises in `docs/product-brief.md`.",
        ),
        qa_gates=[
            "OpenAPI/resource contracts are documented before persistence choices harden.",
            "Health/readiness behavior and config expectations are represented in starter code or docs.",
            "Route, schema, service, repository, auth-boundary, and validation-error tests are seeded or planned.",
            "DevOps has explicit notes for environment variables, local run, and deployment assumptions.",
        ],
        example_prd_prompt="Draft a PRD for a Python FastAPI MVP covering resources, endpoint contracts, auth boundaries, and local test fixtures.",
    ),
    "electron-desktop-app": ProjectTemplate(
        key="electron-desktop-app",
        label="Electron desktop app",
        description="Cross-platform desktop MVP with main/preload/renderer separation, local data, and packaging seams.",
        discovery_focus=(
            "primary desktop workflow, offline expectations, and local file/data access",
            "platform targets, auto-update, packaging, permissions, and native integrations",
            "security boundaries between main, preload, renderer, and untrusted content",
        ),
        engineering_defaults=(
            "keep Electron main, preload bridge, renderer UI, and shared domain logic separate",
            "prefer explicit IPC contracts and avoid exposing broad Node APIs to the renderer",
            "document local storage, file-system, and packaging assumptions before adding native deps",
        ),
        qa_focus=(
            "IPC contract tests, renderer state checks, file permission failures, and offline flows",
            "smoke scripts for app launch, main-window creation, and settings persistence",
        ),
        iteration_hooks=(
            "record platform-specific UX findings and packaging constraints as Delivery notes",
            "keep native integrations behind adapters for future Windows/macOS/Linux hardening",
        ),
        recommended_skills=("product", "ux", "frontend", "desktop", "qa", "devops"),
        starter_files={
            "README.md": """# $project_name\n\n$idea\n\nAider Plus scaffolded this as an Electron desktop MVP.\n\n## Company Mode\n- Product clarifies the desktop job-to-be-done and offline/file expectations.\n- UX defines window states, navigation, shortcuts, and platform conventions.\n- Engineering keeps main, preload, renderer, IPC, and domain logic separated.\n- Delivery/DevOps document packaging, signing, updates, and platform constraints.\n\n## Suggested Structure\n- `electron/main/` — app lifecycle and native integration adapters\n- `electron/preload/` — narrow IPC bridge contracts\n- `renderer/` — UI, screens, and client state\n- `src/domain/` — framework-independent product logic\n- `tests/` — IPC, domain, and renderer smoke checks\n""",
            "electron/main/README.md": "Main-process lifecycle, windows, menus, and native integration adapters live here.\n",
            "electron/preload/README.md": "Expose narrow, documented IPC bridge contracts here.\n",
            "renderer/README.md": "Renderer UI screens, states, and accessibility notes live here.\n",
            "src/domain/README.md": "Framework-independent desktop product logic lives here.\n",
            "tests/README.md": "Add IPC contract, domain, renderer, and launch smoke checks here.\n",
        },
        post_creation_instructions=(
            "Ask Product/UX to identify platform targets before adding packaging dependencies.",
            "Keep IPC contracts documented and security-reviewed before exposing filesystem access.",
            "Document packaging/signing/update assumptions even if they remain TODOs in v0.",
        ),
        qa_gates=[
            "Desktop platform targets, offline expectations, and local data boundaries are documented.",
            "IPC surface is narrow, named, and reviewed before renderer code can access local resources.",
            "Renderer, domain logic, launch smoke, settings persistence, and permission failures have QA coverage planned.",
            "Delivery notes include packaging, signing, auto-update, and platform-specific TODOs.",
        ],
        example_prd_prompt="Draft a PRD for an Electron desktop MVP with offline-first workflows, secure IPC, local data, and packaging assumptions.",
    ),
    "data-dashboard": ProjectTemplate(
        key="data-dashboard",
        label="Data dashboard",
        description="Analytics dashboard MVP with ingestion fixtures, metric definitions, charts, filters, and export seams.",
        discovery_focus=(
            "source systems, freshness, metric definitions, owners, and sensitive fields",
            "decision workflows, dashboard audiences, filters, and drill-down questions",
            "export/sharing requirements, data quality risks, and refresh cadence",
        ),
        engineering_defaults=(
            "separate ingestion fixtures, transforms, metric contracts, and presentation adapters",
            "make chart contracts and sample data deterministic so QA can verify trends",
            "document privacy and retention assumptions before connecting live data sources",
        ),
        qa_focus=(
            "metric correctness, dirty/missing data, filter combinations, chart contracts, and exports",
            "fixture-driven regression tests for representative edge cases",
        ),
        iteration_hooks=(
            "record metric definition changes and data-quality findings as Product memory",
            "keep source adapters replaceable so Delivery can connect production data later",
        ),
        recommended_skills=("product", "data", "ux", "frontend", "qa", "devops"),
        starter_files={
            "README.md": """# $project_name\n\n$idea\n\nAider Plus scaffolded this as a data dashboard MVP with deterministic sample data and explicit metric contracts.\n\n## Company Mode\n- Product owns metric definitions, audiences, and decisions supported by the dashboard.\n- UX owns chart hierarchy, filters, empty states, and explainability.\n- Engineering keeps ingestion, transforms, presentation, and exports separate.\n- QA verifies metrics against fixtures and dirty-data edge cases.\n\n## Suggested Structure\n- `data/sample/` — representative fixtures\n- `src/ingestion/` — source readers and refresh adapters\n- `src/metrics/` — metric definitions and calculation contracts\n- `src/presentation/` — chart/view/export adapters\n- `tests/` — fixture-driven metric and chart contract tests\n""",
            "data/sample/README.md": "Place small, sanitized fixtures with expected metric outcomes here.\n",
            "src/ingestion/README.md": "Source readers, freshness checks, and adapters live here.\n",
            "src/metrics/README.md": "Metric definitions, calculations, and ownership notes live here.\n",
            "src/presentation/README.md": "Chart contracts, views, filters, and export adapters live here.\n",
            "tests/README.md": "Add fixture-driven metric, dirty-data, filter, chart, and export tests here.\n",
        },
        post_creation_instructions=(
            "Confirm metric definitions and sample data before building charts.",
            "Keep live-source credentials out of the repo and use sanitized fixtures for v0.",
            "Ask QA to pin expected metric outcomes so future iterations catch regressions.",
        ),
        qa_gates=[
            "Metric definitions, source freshness, owners, and sensitive fields are explicit in Product docs.",
            "Sample fixtures include expected metric outcomes for QA and Engineering regression tests.",
            "UX documents chart hierarchy, filters, empty states, dirty-data states, and export expectations.",
            "DevOps notes live-source credentials, privacy, retention, and refresh cadence before production data is connected.",
        ],
        example_prd_prompt="Draft a PRD for a data dashboard MVP covering audiences, metric definitions, source fixtures, charts, filters, and exports.",
    ),

    "data-dashboard-streamlit": ProjectTemplate(
        key="data-dashboard-streamlit",
        label="Streamlit data dashboard",
        description="Streamlit analytics MVP with fixtures, metric contracts, interactive filters, and export-ready views.",
        discovery_focus=(
            "dashboard audience, metric glossary, source freshness, and data sensitivity",
            "primary questions, filter dimensions, chart hierarchy, and export needs",
            "fixture strategy, refresh cadence, and deployment/sharing assumptions",
        ),
        engineering_defaults=(
            "keep Streamlit page code thin by separating ingestion, metrics, and view models",
            "ship with sanitized fixtures and deterministic metric calculations before live connectors",
            "document secrets, caching, and deployment assumptions in the starter docs",
        ),
        qa_focus=(
            "metric fixture correctness, filter interactions, missing data states, and export contracts",
            "smoke coverage for Streamlit entrypoint plus pure tests for transforms and metrics",
        ),
        iteration_hooks=(
            "record metric glossary and chart review decisions after each Product/UX pass",
            "keep source adapters replaceable so live data can be added without rewriting views",
        ),
        recommended_skills=["product", "data", "ux", "python", "qa", "devops"],
        starter_files={
            "README.md": """# $project_name

$idea

Aider Plus scaffolded this as a Streamlit data dashboard MVP with deterministic fixtures and metric contracts.

## Suggested Structure
- `app.py` — thin Streamlit entrypoint and page composition
- `data/sample/` — sanitized fixtures
- `src/$python_package/ingestion/` — source readers and refresh seams
- `src/$python_package/metrics/` — pure metric calculations and glossary
- `src/$python_package/presentation/` — chart/table/export view models
- `tests/` — fixture, metric, filter, and export checks
""",
            "app.py": """\"\"\"Streamlit entrypoint for $project_name.\"\"\"


def main() -> None:
    \"\"\"Placeholder entrypoint until Streamlit dependencies are selected.\"\"\"
    print(\"$project_name dashboard scaffold is ready.\")


if __name__ == \"__main__\":
    main()
""",
            "data/sample/README.md": "Place sanitized fixtures and expected metric outcomes here.\n",
            "src/$python_package/__init__.py": "\"\"\"$project_name dashboard package.\"\"\"\n",
            "src/$python_package/ingestion/README.md": "Source readers, validation, and cache boundaries live here.\n",
            "src/$python_package/metrics/README.md": "Metric definitions and pure calculations live here.\n",
            "src/$python_package/presentation/README.md": "Streamlit-ready chart, filter, table, and export view models live here.\n",
            "tests/README.md": "Add fixture-driven metric, filter, export, and app smoke tests here.\n",
        },
        post_creation_instructions=(
            "Confirm metric glossary and fixture expectations before styling charts.",
            "Keep Streamlit secrets and live source credentials out of the repository.",
            "Ask QA to pin metric outcomes from fixtures before live data connectors are added.",
        ),
        qa_gates=[
            "Metric glossary, fixture rows, and expected outputs are documented together.",
            "Pure metric tests pass without running Streamlit or connecting live data sources.",
            "Filters, empty states, dirty-data states, and exports are represented in UX/QA notes.",
            "Deployment notes cover secrets, caching, sharing, and refresh cadence.",
        ],
        example_prd_prompt="Draft a PRD for a Streamlit dashboard MVP covering audiences, metric glossary, fixture data, filters, chart views, exports, and deployment notes.",
    ),
    "cli-tool-python": ProjectTemplate(
        key="cli-tool-python",
        label="Python CLI tool",
        description="Python package CLI MVP with subcommands, config, deterministic output, docs, and test seams.",
        discovery_focus=(
            "primary command verbs, inputs, outputs, and automation workflows",
            "configuration sources, environment variables, defaults, and precedence rules",
            "failure modes, exit codes, stdout/stderr expectations, and packaging target",
        ),
        engineering_defaults=(
            "separate CLI parsing, command handlers, config loading, and core domain logic",
            "keep command output deterministic and snapshot-friendly for tests",
            "document packaging, installation, shell completion, and release assumptions",
        ),
        qa_focus=(
            "argument parsing, help text, config precedence, exit codes, and golden outputs",
            "handler tests that avoid network and filesystem side effects unless fixtures are explicit",
        ),
        iteration_hooks=(
            "record flag naming, defaults, and output format decisions as Product memory",
            "keep command handlers small so future subcommands can be added without regressions",
        ),
        recommended_skills=["product", "python", "backend", "qa", "devops"],
        starter_files={
            "README.md": """# $project_name

$idea

Aider Plus scaffolded this as a Python CLI MVP.

## Suggested Structure
- `src/$python_package/cli.py` — argument parsing and command dispatch
- `src/$python_package/commands/` — small command handlers
- `src/$python_package/config.py` — config and environment precedence
- `src/$python_package/core/` — reusable domain logic
- `tests/` — parser, handler, config, exit-code, and golden-output checks
""",
            "src/$python_package/__init__.py": "\"\"\"$project_name CLI package.\"\"\"\n",
            "src/$python_package/cli.py": """\"\"\"CLI entrypoint for $project_name.\"\"\"


def main(argv: list[str] | None = None) -> int:
    \"\"\"Placeholder CLI entrypoint until commands are implemented.\"\"\"
    _ = argv
    print(\"$project_name CLI scaffold is ready.\")
    return 0
""",
            "src/$python_package/commands/README.md": "Small command handlers live here; keep I/O contracts explicit.\n",
            "src/$python_package/core/README.md": "Reusable domain logic that can be tested without CLI parsing lives here.\n",
            "src/$python_package/config.py": "\"\"\"Configuration loading placeholder for $project_name.\"\"\"\n",
            "tests/README.md": "Add parser, config precedence, exit-code, golden output, and handler tests here.\n",
        },
        post_creation_instructions=(
            "Confirm command verbs and output contracts before choosing CLI dependencies.",
            "Keep network, filesystem, and credential-dependent behavior behind injectable adapters.",
            "Ask QA to define golden outputs and exit-code expectations before broadening subcommands.",
        ),
        qa_gates=[
            "Every documented command has help text, success output, failure output, and exit-code expectations.",
            "Config precedence and environment variables are documented and tested with fixtures.",
            "Core command logic is testable without invoking a shell or external network.",
            "Release notes cover installation, packaging, versioning, and backward-compatible output changes.",
        ],
        example_prd_prompt="Draft a PRD for a Python CLI MVP covering command verbs, input/output contracts, config precedence, exit codes, and packaging notes.",
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

## Example PRD Prompt
$example_prd_prompt

## Delivery Loop
- Product: clarify the core user, promise, MVP scope, and acceptance criteria.
- UX: define first-run flow, primary screens, accessible states, and review notes.
- Engineering: keep implementation repo-native, modular, testable, and dependency-aware.
- Delivery: translate department decisions into thin milestones and release notes.
- DevOps: document local run, configuration, CI, deployment, and operational assumptions.

## Recommended Skills
$recommended_skills_markdown

## QA Gates
$qa_gates_markdown

## Post-Creation Instructions
$post_creation_instructions_markdown

## Next Iteration Notes
Capture user approvals, QA findings, metrics, risks, and follow-up scope here so
future company runs can keep evolving the same Git repository.
""",
    "docs/company-mode.md": """# Company Mode Handoff Guide

This repository was scaffolded by `aider company new` for `$template_label`. Use
these seams to keep Product → UX → Engineering → Delivery → DevOps aligned.

## Department Gates
1. **Product** updates `docs/product-brief.md` with users, scope, acceptance criteria, and open questions.
2. **UX** records flow, screen, accessibility, empty/error/loading, and approval notes.
3. **Engineering** implements small vertical slices using the template folders as boundaries.
4. **QA** adds smoke or unit checks for critical paths and documents skipped checks.
5. **Delivery/DevOps** documents release, run, configuration, and deployment assumptions.

## Template Guidance
- Template: `$template_label` (`$template_key`)
- Recommended skills: $recommended_skills_inline
- PRD seed: $example_prd_prompt

## QA Gates
$qa_gates_markdown

## Iteration Rule
Prefer adding explicit notes, tests, and adapters over replacing scaffolding in a
large rewrite. Future Company runs should be able to read this repo and continue
from the current product state.
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


def _markdown_bullets(items) -> str:
    return "\n".join(f"- {item}" for item in items)


def _default_post_creation_steps() -> list[str]:
    return [
        "Run Company Mode once to refine the MVP brief before adding major dependencies.",
    ]


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
        "recommended_skills_inline": ", ".join(template.recommended_skills)
        or "generalist",
        "recommended_skills_markdown": _markdown_bullets(
            template.recommended_skills or ["generalist"]
        ),
        "post_creation_instructions_markdown": _markdown_bullets(
            template.post_creation_steps() or _default_post_creation_steps()
        ),
        "qa_gates_markdown": _markdown_bullets(template.all_qa_gates()),
        "example_prd_prompt": template.example_prd_prompt
        or f"Draft a concise PRD for {template.label} covering users, scope, acceptance criteria, risks, and release notes.",
    }
    files: dict[str, str] = {
        ".aider/company/product.json": json.dumps(
            {
                "name": project_name.strip(),
                "slug": project_slug,
                "template": template.key,
                "template_label": template.label,
                "idea": idea.strip(),
                "recommended_skills": list(template.recommended_skills),
                "post_creation_instructions": template.post_creation_steps(),
                "example_prd_prompt": values["example_prd_prompt"],
                "qa_gates": template.all_qa_gates(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    }
    skill_files = {
        f".aider/skills/{skill}/SKILL.md": (
            f"# {skill.title()} Skill\n\n"
            f"Use this placeholder to capture {skill} guidance learned while building `$project_name`.\n"
            "Keep guidance concise, repo-specific, and safe for future Company runs.\n"
        )
        for skill in (template.recommended_skills or ["generalist"])
    }
    for path, content in {
        **COMMON_STARTER_FILES,
        **TEMPLATE_STARTER_FILES.get(template.key, {}),
        **dict(template.all_starter_files()),
        **skill_files,
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
            "Recommended skills to activate or emulate:",
            bullets(tuple(template.recommended_skills or ["generalist"])),
            "",
            "Example Product/PRD seed for this template:",
            template.example_prd_prompt
            or f"Draft a concise PRD for {template.label} covering users, scope, acceptance criteria, risks, and release notes.",
            "",
            "Use the Aider Plus delivery loop: Product -> UX -> Engineering -> Delivery -> DevOps.",
            "Keep QA explicit in that loop; legacy shorthand is Product -> UX -> Engineering -> QA -> DevOps.",
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
            "QA gates:",
            bullets(tuple(template.all_qa_gates())),
            "",
            "Iteration and memory hooks:",
            bullets(template.iteration_hooks),
            "",
            "Post-creation instructions:",
            bullets(
                tuple(
                    template.post_creation_steps()
                    or ["Run Product discovery before adding major dependencies."]
                )
            ),
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
            "- Include Delivery and DevOps handoff notes for release readiness.",
        ]
    )
