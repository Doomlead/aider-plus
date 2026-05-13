"""Zero-to-MVP product templates for Aider Plus Company Mode.

The templates are intentionally lightweight. They do not scaffold files by
copying a framework skeleton; instead they ground Product, UX, Engineering,
QA, and DevOps prompts with product-shape-specific expectations so the same
repo-native Aider workflow can create v0 and keep iterating afterward.
"""

from __future__ import annotations

from dataclasses import dataclass


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
