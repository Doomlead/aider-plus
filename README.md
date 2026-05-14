# Aider Plus

Aider Plus is a Git-native product studio for building and iterating software with
Aider. It preserves Aider's strongest advantage—real repositories, branches,
diffs, tests, and reviewable commits—then layers a lightweight Company Mode,
Nanobot-style COO, browser/desktop surfaces, memory, MCP tooling, and a thin
warehouse registry on top.

The warehouse is intentionally **not** a clone of ChatDev's `WareHouse` runtime.
In Aider Plus, a warehouse is a registry of normal Git-backed product repos. It
handles discovery, naming, metadata, and shared COO memory while each product
remains a regular repo that can be opened, edited, tested, committed, and pushed
with standard Git workflows.

---

## What is new: warehouse-backed Company Mode

Company Mode can now create new applications inside a central warehouse:

```bash
aider warehouse init ~/AiderPlusWarehouse
aider company new "Build a habit tracker" --name habit-tracker --template nextjs-app --warehouse ~/AiderPlusWarehouse
aider warehouse list --warehouse ~/AiderPlusWarehouse
aider warehouse open habit-tracker --warehouse ~/AiderPlusWarehouse
aider warehouse status --warehouse ~/AiderPlusWarehouse
```

A warehouse has this shape:

```text
AiderPlusWarehouse/
  warehouse.json              # registry of product repos and metadata
  products/                   # Git-backed product repositories
    habit-tracker/
      .git/
      README.md
      docs/product-brief.md
      .aider/company/product.json
      ...template starter files...
  .aider/coo/                 # cross-product COO memory
```

Each product under `products/<product-slug>/` is still an ordinary Git repo.
The warehouse only manages registration, naming, starter structure, discovery,
and cross-project memory.

---

## Core principles

- **Repo-native first.** Company Mode operates inside real Git repositories, not
  a synthetic project model.
- **Warehouse as registry.** The warehouse discovers and tracks product repos; it
  does not replace their Git history or local tooling.
- **Product-studio feel.** `aider company new` gives the ChatDev-style feeling of
  starting a product in a studio while keeping Aider's iterative coding loop.
- **Thin chat surfaces.** Browser, desktop, CLI, API, MCP, and Discord surfaces
  normalize messages and delegate to shared runtime paths.
- **One orchestrator path.** Product building flows through
  Product → UX → Engineering → Reviewer → QA → DevOps.
- **Observable memory and decisions.** COO routing, project memory, approvals,
  audits, and lifecycle events are stored in files and visible in the GUIs.

---

## Quickstart

### Install

```bash
python -m pip install -e '.[browser]'
```

The browser GUI uses the `browser` extra. The desktop GUI uses Tkinter from the
Python standard library.

### Create a warehouse

```bash
aider warehouse init ~/AiderPlusWarehouse
```

This creates `warehouse.json`, a `products/` directory for product repos, and
`.aider/coo/` for shared COO memory.

### Create a new application

```bash
aider company new "Build a habit tracker with streaks" \
  --name habit-tracker \
  --template nextjs-app \
  --warehouse ~/AiderPlusWarehouse
```

Aider Plus will:

1. create `~/AiderPlusWarehouse/products/habit-tracker/`;
2. initialize it as a Git repo;
3. register it in `warehouse.json`;
4. scaffold a coherent MVP starter structure from `aider/company/templates.py`;
5. switch into that repo and run the normal Aider Company implementation loop.

### Inspect warehouse state

```bash
aider warehouse list --warehouse ~/AiderPlusWarehouse
aider warehouse status --warehouse ~/AiderPlusWarehouse
aider warehouse open habit-tracker --warehouse ~/AiderPlusWarehouse
```

`warehouse open` prints the product repo path so scripts or shells can `cd` into
it.

### Preview without changing files

```bash
aider company new "Build a Stripe webhook API" \
  --name billing-hooks \
  --template fastapi-backend \
  --warehouse ~/AiderPlusWarehouse \
  --dry-plan
```

Dry plans show the warehouse path, product repo path, and Company Mode brief
without initializing a repo or invoking the coder.

---

## Templates

List available templates:

```bash
aider company templates
```

Current zero-to-MVP templates include:

- `saas-dashboard` — authenticated dashboard, metrics, CRUD, admin views.
- `nextjs-app` — modern React/Next.js-style product structure.
- `fastapi-backend` — Python API with routes, schemas, services, and adapters.
- `cli-tool` — command-line product with subcommands and tests.
- `discord-bot` — bot commands, events, permissions, and operational safety.
- `browser-extension` — manifest, popup/options UI, content scripts, and tests.
- `data-app` — ingestion, transforms, charts, exports, and fixtures.
- `internal-admin` — back-office workflows, roles, audit, and approvals.

Templates are lightweight by design. They create starter directories, README
files, product metadata, and template-specific seams so Company Mode can build a
small coherent MVP without dumping a full framework skeleton into the repo.

---

## CLI reference

### Company commands

```bash
aider company templates
```

Print the template catalog.

```bash
aider company create <idea> [--template TEMPLATE] [--name PROJECT_NAME] [--dry-plan] [-- AIDER_ARGS...]
```

Run the zero-to-MVP Company brief in the current repo. Use this when you already
have a product repo open and want Aider Plus to extend it.

```bash
aider company new <idea> [--template TEMPLATE] [--name PRODUCT_NAME] [--warehouse PATH] [--dry-plan] [-- AIDER_ARGS...]
```

Create or reuse a product repo inside `PATH/products/<slug>/`, register it in the
warehouse, scaffold the template starter structure, switch into the repo, then
run the Company implementation loop. Aider arguments after `--` are passed
through to the normal Aider startup.

### Warehouse commands

```bash
aider warehouse init [PATH]
```

Initialize a warehouse. If `PATH` is omitted, Aider Plus uses a local default
warehouse path.

```bash
aider warehouse list [--warehouse PATH]
```

List registered products with slug, display name, template, and repo path.

```bash
aider warehouse open PRODUCT [--warehouse PATH]
```

Print the registered product repo path.

```bash
aider warehouse status [--warehouse PATH]
```

Show registry path, products directory, product counts, missing repos, and shared
COO memory location.

---

## Architecture

```text
User / CEO
  |
  v
CLI, Browser GUI, Desktop GUI, API, MCP, or chat adapter
  |
  v
NanobotCOO personal-assistant loop
  |  - reads durable COO session history
  |  - pulls COO profile, project memory, skills, and warehouse context
  |  - decides whether to answer, clarify, remember, inspect, use tools, or delegate
  |
  +--> direct response / clarification / memory update / status brief
  |
  v
CompanyOrchestrator
  |
  v
Product -> UX -> Engineering -> Reviewer -> QA -> DevOps
  |
  v
Git-backed product repo + deliverables + audit log + approvals + memory
```

The orchestrator is the canonical product-building path. Chat and GUI surfaces
should not implement their own Product, QA, approval, audit, status, or deployment
logic. They send messages in and render shared results out.

---

## GUI surfaces

### Browser GUI

```bash
aider --browser
```

The Streamlit browser GUI exposes chat targets, settings, Company dashboard,
approvals, audit log, project memory, and guide pages.

### Desktop GUI

```bash
aider --desktop
```

The native desktop app uses Tkinter only. It exposes the same Company workflow,
approvals, audit, dashboard, settings, and per-agent chat paths as the browser
GUI without requiring Streamlit or a browser.

### Chat targets

- **Direct Aider** — classic Git-aware pair-programming.
- **Company Workflow** — COO-led Product → UX → Engineering → Reviewer → QA → DevOps.
- **COO** — briefing, status, memory, clarifications, routing, and delegation.
- **Product** — requirements, ambiguity checks, PRDs, and launch criteria.
- **UX** — flows, states, accessibility, interaction details, and design specs.
- **Engineering** — implementation plans and code changes.
- **Reviewer** — implementation review and quality checks.
- **QA** — test plans, validation, release confidence, and regression guidance.
- **DevOps** — release, deployment, MCP/ops, rollback, and recovery guidance.

---

## Classic Aider usage

Aider Plus still supports direct Aider workflows:

```bash
aider --model gpt-5.5
```

Run one headless task:

```bash
aider --headless --model gpt-5.5 --msg "Refactor the parser and add tests"
```

`--headless` also works as `--bot-mode` for scripts, queues, service wrappers,
chat workers, and CI jobs.

Start onboarding:

```bash
aider onboard
# or
aider init
```

---

## Settings and configuration

Both browser and desktop use the shared settings manager. Settings are organized
into:

1. **Global Aider** — main, weak, and editor model defaults for direct chat.
2. **Per-Agent Overrides** — model, prompt caching, API key override, and local
   endpoint/setting for COO, Product, UX, Engineering, Reviewer, QA, and DevOps.
3. **Provider Keys** — OpenAI, Anthropic, OpenRouter, and other provider credentials.
4. **Advanced (`.env` + `.aider.conf`)** — extra environment lines and raw
   `.aider.conf.yml` editing.

Example environment overrides:

```bash
AIDER_COMPANY_AGENT_MODELS="coo=gpt-5.5,product=gpt-5.5,engineering=gpt-5.5"
AIDER_COMPANY_MODEL_COO=gpt-5.5
AIDER_COMPANY_CACHING_COO=true
AIDER_COMPANY_CACHING_ENGINEERING=true
AIDER_MCP_ENABLED=true
```

Use **Preview changes** before saving, then **Apply & Restart Company Session** so
new agent loops pick up model, key, caching, `.env`, and `.aider.conf.yml`
updates.

---

## Memory, skills, MCP, and approvals

Aider Plus keeps memory local and inspectable:

- Product repos keep repo-local project memory, audit data, playbook patterns,
  and recent outcomes.
- Warehouses keep shared COO memory under `.aider/coo/` for cross-product context.
- Conversation memory consolidates chat context into project memory.
- Skills and playbook guidance can be pulled into department context before a
  task is executed.
- MCP tool requests can be approval-gated and surfaced in GUI approval flows.

This keeps the machine-learning loop research-friendly: inputs, memory, tool
decisions, routes, and outputs are observable.

---

## Discord integration

Discord is intentionally only a chat app adapter. It can accept chat text and
forward it to headless Aider, but it does not own Company Mode dashboards,
approvals, audit logs, COO status commands, lifecycle buttons, or product
workflow behavior. Use the browser GUI, desktop GUI, CLI, API, or MCP surfaces for
those capabilities.

This makes Discord replaceable with Slack, Matrix, webhooks, or custom chat apps:
implement message normalization and session identity, then hand the message to
the shared runtime.

---

## Development

Useful checks:

```bash
python -m py_compile aider/company/templates.py aider/company/warehouse.py aider/company/cli.py aider/main.py
python -m pytest tests/company/test_warehouse_cli.py tests/company/test_zero_to_mvp_cli.py
```

Important files:

- `aider/company/templates.py` — zero-to-MVP prompts and template starter structures.
- `aider/company/warehouse.py` — thin warehouse registry for Git-backed product repos.
- `aider/company/cli.py` — `aider company ...` and `aider warehouse ...` parsing/handlers.
- `aider/company/coo.py` — Nanobot-style COO sessions, action decisions, memory, status, bus, and delegation.
- `aider/company/orchestrator.py` — workflow, approvals, lifecycle, context, handoffs, and audit coordination.
- `aider/company/departments/` — Product, UX, Engineering, Reviewer, QA, and DevOps implementations.
- `aider/company/surface_messages.py` — shared lifecycle/status/approval/audit message formatting.
- `aider/memory/` — conversation memory, project memory, retrieval, consolidation, and pattern extraction.
- `aider/mcp/` — MCP configuration, manager, adapters, and server helpers.
- `aider/gui.py` — Streamlit browser GUI.
- `aider/desktop.py` — Tkinter desktop GUI.
- `tests/company/` and `tests/mcp/` — focused workflow, COO, GUI settings, approvals, lifecycle, and MCP coverage.

---

## Safety note

Aider Plus is experimental software. Use Git branches, review generated changes,
keep approval gates enabled for important work, and treat MCP/deployment actions
as operations that need human oversight.
