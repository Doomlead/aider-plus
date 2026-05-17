# Zero-to-MVP golden path

Aider Plus is designed around one product promise:

> **ChatDev-like product creation, Aider-like continuous development.**
>
> A software company that keeps working after v0.

The `aider company create` command keeps the current-repo path for existing
projects. The `aider company new` command adds a thin warehouse manager for new
product work: it creates or reuses a Git-backed repo under the warehouse
`products/` directory, records it in `warehouse.json`, scaffolds a coherent MVP
starter structure from `aider/company/templates.py`, writes Company metadata,
placeholder skills, post-creation hooks, QA gates, and an initial scaffold commit,
then runs the same template-grounded Company brief through Aider's repo-aware implementation loop
so the result remains reviewable, testable, and ready for future iteration.

## How it works under the hood

For the full contributor walkthrough, read the
[First Code Tour](../architecture/first-code-tour.md). In short,
`aider/main.py` parses `aider company create` and `aider company new`, the
warehouse/template layer prepares a normal Git repository and a template-grounded
Company brief, the COO/orchestrator route that work through Product, UX,
Delivery, Engineering, Reviewer, QA, Delivery, and DevOps, and shared
memory/skills/EventBus/audit services keep the run inspectable. The daemon uses
the same concepts for issue-backed workspaces, but adds proof-of-work files and
tracker updates for human review.

## Guided onboarding flow

Run the first-time setup before creating your first product:

```bash
aider company init
```

The `CompanyOnboarding` flow walks through the core studio setup:

1. Initialize the warehouse that stores Git-backed product repositories.
2. Pick the default `aider company new --template ...` starter.
3. Configure a GitHub token/repo pair for the issue daemon, or leave it blank for local tracker use.
4. Choose preferred department models and prompt caching for Product, UX, Engineering, Reviewer, QA, and DevOps.
5. Validate whether the provider API keys inferred from those models are already available in the environment.
6. Decide whether MCP integrations should be enabled for external tools/context.
7. Generate a tailored `.env.example`, `AIDER_WORKFLOW.md`, and `.aider/company/workflow.yml` with the quickstart commands and daemon entry point.
8. Optionally create the first Git-backed product repo immediately; `--yes` auto-runs that final step with defaults or `--product-idea`/`--product-name`.

The same helper is exposed in the Streamlit browser UI and the native Tkinter desktop UI under **Onboarding / Quick Start**. Use `--skip-onboarding` on normal `aider` launches when you do not want the first-run offer.

```bash
aider company init --warehouse ~/AiderPlusWarehouse --template nextjs-saas --github-repo owner/repo --model gpt-5.5 --enable-mcp --product-idea "Build my MVP" --product-name my-mvp --yes
```

## Command

```bash
aider company create "Build a simple habit tracker web app with login, dashboard, and streaks"
# or create a product repo under a warehouse first
aider warehouse init ~/AiderPlusWarehouse
aider company new "Build a simple habit tracker web app with login, dashboard, and streaks" --name habit-tracker --template nextjs-saas --warehouse ~/AiderPlusWarehouse
```

Choose a product shape with `--template`:

```bash
aider company create "Build a webhook API for Stripe events" --template fastapi-api
```

Pass normal Aider options after `--`:

```bash
aider company create "Build a CLI for exporting reports" --template python-cli -- --model gpt-5.5
```

Preview the generated Company brief without calling a model:

```bash
aider company create "Build a habit tracker" --template nextjs-saas --dry-plan
```

List templates:

```bash
aider company templates
```

## Warehouse commands

The warehouse is a registry of normal Git repositories, not a replacement for
Git repos:

```bash
aider warehouse init ~/AiderPlusWarehouse
aider warehouse list --warehouse ~/AiderPlusWarehouse
aider warehouse open habit-tracker --warehouse ~/AiderPlusWarehouse
aider warehouse status --warehouse ~/AiderPlusWarehouse
```

Each product lives under `<warehouse>/products/<slug>/` as an independently
editable Git repo for classic Aider, Company Mode, desktop, browser, CI, and
normal Git tooling.

## Templates

The built-in templates ground Product, UX, Engineering, Delivery, QA, DevOps,
and iteration prompts for common MVP shapes. Each template includes recommended
skills, starter files, QA gates, post-creation instructions, and an example PRD prompt seed.

- `nextjs-saas` — SaaS onboarding, workspace dashboard, billing seams, settings,
  and auth/analytics/data provider adapters.
- `fastapi-api` — contract-first API with routes, schemas, services,
  repositories, health/readiness checks, and OpenAPI/test guidance.
- `python-cli` — Python package CLI with subcommands, config precedence,
  deterministic output, exit codes, packaging notes, and tests.
- `electron-desktop` — desktop MVP with main/preload/renderer separation,
  secure IPC, local data, offline behavior, and packaging seams.
- `streamlit-dashboard` — Streamlit dashboard with fixture-backed metrics,
  filters, chart/export seams, secrets notes, and deployment guidance.
- `data-dashboard` — dashboard/data workflow with fixtures, metric definitions,
  charts, filters, exports, and data-quality checks.
- `discord-bot` — bot with commands, event handling, permissions, and
  operational safeguards.
- `browser-extension` — extension with manifest, popup/options UI, content
  scripts, and permissions.
- `internal-admin` — back-office UI with roles, workflows, destructive-action
  safeguards, and auditability.

Template Aliases: legacy names such as `nextjs-app`, `python-fastapi-api`,
`fastapi-backend`, `cli-tool-python`, `electron-desktop-app`,
`data-dashboard-streamlit`, `saas-dashboard`, `cli-tool`, and `data-app` remain
accepted for backward compatibility. They resolve to the canonical names above
and produce a deprecation note.

Example starts:

```bash
aider company new "Build a founder revenue dashboard with cohort charts" --template streamlit-dashboard --name founder-metrics --warehouse ~/AiderPlusWarehouse
aider company new "Build a Python CLI that exports reports from CSV files" --template python-cli --name report-exporter --warehouse ~/AiderPlusWarehouse
aider company new "Build a secure offline notes desktop app" --template electron-desktop --name secure-notes --warehouse ~/AiderPlusWarehouse
```

Generated starter repos include:

```text
.aider/company/product.json        # template metadata, skills, QA gates, and PRD seed
.aider/company/post-creation.md    # first-run hooks for the selected template
.aider/skills/<skill>/SKILL.md     # placeholder skill notes for future runs
docs/company-mode.md              # Product → UX → Engineering → Delivery → DevOps handoff guide
docs/product-brief.md             # Product brief and iteration notes
```

## Full lifecycle example

For the prompt:

```text
Build a simple habit tracker web app with login, dashboard, and streaks.
```

A healthy run should produce artifacts like these:

1. **Initial idea** — the user's one-sentence product request.
2. **Clarification questions** — Product asks only when target users, success,
   or constraints are too vague.
3. **PRD** — Product captures goals, user stories, acceptance criteria, success
   metrics, technical considerations, out-of-scope items, and open questions.
4. **UX design spec** — UX describes routes, screens, reusable components, data
   contracts, loading/error/empty states, responsive behavior, and accessibility.
5. **Engineering diff** — Engineering creates or extends the repo using the PRD,
   design handoff, schema-gate results, and relevant playbook guidance.
6. **QA failure** — QA may fail the first pass with structured feedback such as
   missing streak edge-case coverage or inaccessible form labels.
7. **Engineering revision** — Engineering receives QA feedback and applies a
   bounded fix instead of starting over.
8. **Passing QA** — checks pass or no-test constraints are explicitly recorded.
9. **Release approval** — the CEO can approve, reject, or request changes before
   DevOps/release completion.
10. **DevOps result** — release/deployment notes are recorded, including any
    environment variables or external-service TODOs.
11. **Post-mortem playbook entry** — Aider Plus extracts reusable lessons, such
    as preferred auth boundaries, dashboard empty-state expectations, QA edge
    cases, and deployment gotchas.

## Why this differs from one-shot generators

The point is not to generate a throwaway v0. The generated brief tells the agent
loop to:

- inspect the existing repository before choosing files or frameworks;
- create the smallest coherent MVP only when the repo is empty;
- extend existing frameworks instead of replacing them;
- document how to run and iterate on the MVP;
- add tests or smoke checks where practical;
- keep changes reviewable;
- store recurring lessons as playbook guidance.

The retrieval-aware playbook is the moat: future work can retrieve relevant
lessons without dumping the entire project history into every prompt.
