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
placeholder skills, and post-creation hooks, then runs the same
template-grounded Company brief through Aider's repo-aware implementation loop
so the result remains reviewable, testable, and ready for future iteration.

## Command

```bash
aider company create "Build a simple habit tracker web app with login, dashboard, and streaks"
# or create a product repo under a warehouse first
aider warehouse init ~/AiderPlusWarehouse
aider company new "Build a simple habit tracker web app with login, dashboard, and streaks" --name habit-tracker --template nextjs-saas --warehouse ~/AiderPlusWarehouse
```

Choose a product shape with `--template`:

```bash
aider company create "Build a webhook API for Stripe events" --template python-fastapi-api
```

Pass normal Aider options after `--`:

```bash
aider company create "Build a CLI for exporting reports" --template cli-tool -- --model gpt-5.5
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
skills, starter files, post-creation instructions, and an example PRD prompt seed.

- `nextjs-saas` — SaaS onboarding, workspace dashboard, billing seams, settings,
  and auth/analytics/data provider adapters.
- `python-fastapi-api` — contract-first API with routes, schemas, services,
  repositories, health/readiness checks, and OpenAPI/test guidance.
- `electron-desktop-app` — desktop MVP with main/preload/renderer separation,
  secure IPC, local data, offline behavior, and packaging seams.
- `data-dashboard` — dashboard with fixtures, metric definitions, charts,
  filters, exports, and data-quality checks.
- `saas-dashboard` — authenticated dashboard app with metrics, CRUD workflows,
  and admin views.
- `cli-tool` — command-line product with subcommands, config, help text, and
  tests.
- `fastapi-backend` — Python API service with routes, schemas, persistence
  boundaries, and tests.
- `nextjs-app` — React/Next.js product with routes, components, state, and UI
  tests.
- `discord-bot` — bot with commands, event handling, permissions, and
  operational safeguards.
- `browser-extension` — extension with manifest, popup/options UI, content
  scripts, and permissions.
- `data-app` — interactive data workflow with ingestion, transforms, charts,
  and exports.
- `internal-admin` — back-office UI with roles, workflows, destructive-action
  safeguards, and auditability.

Example starts:

```bash
aider company new "Build a founder revenue dashboard with cohort charts" --template data-dashboard --name founder-metrics --warehouse ~/AiderPlusWarehouse
aider company new "Build a secure offline notes desktop app" --template electron-desktop-app --name secure-notes --warehouse ~/AiderPlusWarehouse
```

Generated starter repos include:

```text
.aider/company/product.json        # template metadata and PRD seed
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
