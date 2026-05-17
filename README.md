# Aider Plus

Aider Plus is a Git-native product studio built on top of Aider. It keeps the
core Aider workflow—real repositories, diffs, branches, tests, commits, and
reviewable changes—then adds Company Mode, an operations assistant,
warehouse-backed product creation, browser and desktop control surfaces, local
memory, skills, MCP tooling, Discord/headless adapters, and an issue-driven
Company daemon.

Aider Plus is intentionally **not** a synthetic project runtime. Product work
still happens inside normal Git repositories. The extra layers coordinate people,
agents, memory, approvals, queues, and GUIs around that repo-native loop.

---

## First Time Setup

Start with the guided Company Mode onboarding flow:

```bash
aider company init
```

The onboarding flow initializes a warehouse, chooses the default `aider company new --template ...` template, captures GitHub Issues settings for the Company daemon, records preferred models and prompt-caching choices per department, optionally enables MCP, and writes `AIDER_WORKFLOW.md` in the repo root.

For a non-interactive bootstrap, pass defaults explicitly:

```bash
aider company init --warehouse ./AiderPlusWarehouse --template nextjs-app --github-repo owner/repo --model gpt-5.5 --enable-mcp --yes
```

On first run, Aider Plus offers this setup automatically. Use `--skip-onboarding` if you want to start classic Aider without the Company quickstart prompt. The Streamlit browser UI and Tkinter desktop UI also include an **Onboarding / Quick Start** section that generates the same local quickstart files.

## What Aider Plus adds to upstream Aider

- **Company Mode:** a Product → UX → Delivery → Engineering → Reviewer → QA →
  Delivery → DevOps workflow for turning ideas, issues, or chat requests into
  implementation plans, code changes, validation notes, release execution, and
  audit trails. Delivery owns timelines, milestones, blockers, release readiness,
  and the explicit DevOps handoff; DevOps executes validated builds, packaging,
  tagging, deployment, and release metadata capture.
- **Operations assistant:** a CEO-facing assistant layer that can answer directly,
  ask clarifying questions, remember context, inspect project state, inspect
  learned skills, list daemon workflow state, route work to departments, report
  status, and surface queue/error telemetry.
- **Warehouse-backed product studio:** `aider company new` can create a named
  product repo inside a central warehouse, register it, scaffold starter files,
  and then run Company Mode against the new repo.
- **Zero-to-MVP templates:** richer templates for Next.js SaaS apps, Python
  FastAPI APIs, Electron desktop apps, Streamlit/data dashboards, Python CLI
  tools, SaaS dashboards, Discord bots, browser extensions, and internal admin
  tools. Templates now include recommended skills, starter files, QA gates, post-creation hooks, and PRD
  prompt seeds.
- **Browser and native desktop GUIs:** shared Company dashboards, direct chat,
  per-agent chat tabs, settings editors, approvals, audit views, memory views,
  Delivery summaries, skill quick views, daemon status/proof-of-work panels, and
  guide pages.
- **Headless and chat-adapter operation:** `--headless`/`--bot-mode` supports
  scripted tasks, queues, CI, services, Discord, and future chat adapters.
- **Durable local memory:** conversation summaries, project memory, retrieval,
  playbook pattern extraction, audit history, COO session history, and warehouse
  COO memory are kept as inspectable local files.
- **Procedural skills:** role-scoped `SKILL.md` workflows can be retrieved for
  relevant tasks, usage is tracked, and approval-gated skill proposals can be
  generated from successful audit/playbook patterns.
- **MCP integration:** department agent loops can use Model Context Protocol
  tools through approval-aware adapters and manager configuration.
- **Company daemon:** an issue workflow daemon can pull eligible issues from a
  local JSON tracker or GitHub Issues, prepare per-issue workspaces, run Company
  prompts, write proof-of-work artifacts, update tracker state, attach PRs, and
  require human review.
- **Focused Company seam tests:** Company Mode now has targeted tests for
  Delivery → DevOps handoff readiness, UX DesignSpecV2 schema-gate retry flows,
  skill retrieval/injection, skill proposal approval, daemon partial-success
  runner behavior, mocked GitHub tracker round trips, and shared GUI/desktop/
  Discord event streaming.


## Company daemon with GitHub Issues

The daemon can use GitHub Issues as its tracker without extra service setup:

```bash
export GITHUB_TOKEN=ghp_your_token
export GITHUB_REPO=owner/repo
aider company daemon --workflow AIDER_WORKFLOW.md --tracker github --repo owner/repo --once
```

For production, prefer GitHub App installation credentials when available. Install the optional GitHub extra first if you need GitHub App JWT signing:

```bash
python -m pip install -e '.[github]'
export GITHUB_APP_ID=12345
export GITHUB_APP_INSTALLATION_ID=67890
export GITHUB_APP_PRIVATE_KEY_PATH=/secure/path/aider-company.private-key.pem
export GITHUB_REPO=owner/repo
```

Workflow files can declare `tracker.kind: github`, `tracker.labels`, and a
`tracker.github` section for cache/retry controls and label mappings. The daemon
lists labeled open issues, claims them with an `in_progress` label, posts
progress/proof comments, attaches PR links, retries rate-limited GitHub API
calls, briefly caches issue lists for frequent ticks, and marks completed work as
`done`.

---

## Core principles

- **Repo-native first.** Aider Plus augments Aider; it does not replace Git,
  tests, local tooling, or ordinary reviewable commits.
- **One orchestration path.** CLI, browser, desktop, API/MCP, Discord, and daemon
  entry points should delegate to the same Company runtime instead of duplicating
  Product, QA, approval, audit, deployment, or memory logic.
- **Warehouse as registry.** Warehouses discover and register product repos, but
  each product remains a normal Git repo under `products/<slug>/`.
- **Human-visible state.** Memory, approvals, audit events, lifecycle state,
  skill proposals, daemon proof files, and tracker updates are local and
  inspectable.
- **Thin adapters.** Discord and similar chat integrations normalize messages and
  session identity, then hand off to the shared runtime.
- **Approval by default for risky work.** Tool use, lifecycle gates, release
  decisions, MCP requests, and deployment/recovery guidance are designed to keep
  humans in the loop.

---

## Quickstart

### Install for local development

```bash
python -m pip install -e '.[browser]'
```

The browser GUI uses the `browser` extra. The desktop GUI uses Tkinter from the
Python standard library.

### Run classic Aider

```bash
aider --model gpt-5.5
```

Run one non-interactive task:

```bash
aider --headless --model gpt-5.5 --msg "Refactor the parser and add tests"
```

`--headless` is also available as `--bot-mode`; it disables interactive chat
assumptions and is intended for scripts, queues, service wrappers, chat workers,
and CI jobs.

### Start onboarding

```bash
aider onboard
# or
aider init
```

Onboarding can help create first-run config, select default models, and capture
integration secrets such as a Discord bot token.

---

## Company Mode quickstart

### See available product templates

```bash
aider company templates
```

### Run Company Mode in the current repo

```bash
aider company create "Build a habit tracker with login, streaks, and a dashboard" \
  --template nextjs-saas \
  --name habit-tracker
```

This renders a zero-to-MVP Company brief and runs the normal Aider coding loop in
the current Git repo. Use this when you already have the product repository open.

### Preview a Company plan without changing files

```bash
aider company create "Build a Stripe webhook API" \
  --template python-fastapi-api \
  --dry-plan
```

Dry plans print the Company brief and exit before creating product files or
invoking the coder.

---

## Warehouse-backed product studio

Create a warehouse:

```bash
aider warehouse init ~/AiderPlusWarehouse
```

Create a new product repo inside it:

```bash
aider company new "Build a habit tracker with streaks" \
  --name habit-tracker \
  --template nextjs-saas \
  --warehouse ~/AiderPlusWarehouse
```

Aider Plus will:

1. create `~/AiderPlusWarehouse/products/habit-tracker/`;
2. initialize it as a Git repo;
3. register it in `warehouse.json`;
4. scaffold a coherent starter structure from the selected template;
5. write product metadata, QA gates, placeholder skills, and post-creation hooks under `.aider/`;
6. create an initial scaffold commit so the product repo is review-ready;
7. switch into that repo and inject template-specific Product guidance into the Company implementation loop.

A warehouse has this shape:

```text
AiderPlusWarehouse/
  warehouse.json              # registry of product repos and metadata
  products/                   # Git-backed product repositories
    habit-tracker/
      .git/
      README.md
      docs/product-brief.md
      docs/company-mode.md
      .aider/company/product.json
      .aider/company/post-creation.md
      .aider/skills/.../SKILL.md
      ...template starter files...
  .aider/coo/                 # cross-product COO memory
```

Inspect warehouse state:

```bash
aider warehouse list --warehouse ~/AiderPlusWarehouse
aider warehouse status --warehouse ~/AiderPlusWarehouse
aider warehouse open habit-tracker --warehouse ~/AiderPlusWarehouse
```

`warehouse open` prints the registered product repo path so scripts or shells can
`cd` into it.

Preview a new product without creating it:

```bash
aider company new "Build a Stripe webhook API" \
  --name billing-hooks \
  --template python-fastapi-api \
  --warehouse ~/AiderPlusWarehouse \
  --dry-plan
```

---

## Zero-to-MVP templates

Templates are lightweight by design but now carry richer product metadata. They
create starter directories, README files, `.aider/company/product.json`,
`.aider/company/post-creation.md`, placeholder `.aider/skills/*/SKILL.md` files,
and template-specific seams so Company Mode can build a small coherent MVP
without dumping a full framework distribution into the repo. The generated
Product prompt includes recommended skills, an example PRD seed, discovery
focus, engineering defaults, QA gates, and post-creation guidance.

Current templates:

- `nextjs-saas` — production-shaped SaaS app with onboarding, billing seams, settings, dashboards, and provider adapters.
- `python-fastapi-api` — contract-first FastAPI API with routers, schemas, services, repositories, health checks, and tests.
- `electron-desktop-app` — cross-platform desktop app with main/preload/renderer separation, IPC contracts, local data, and packaging seams.
- `data-dashboard` — analytics dashboard with fixtures, metric definitions, charts, filters, exports, and data-quality checks.
- `data-dashboard-streamlit` — Streamlit analytics dashboard with fixture-backed metrics, filters, chart/export seams, secrets notes, and deployment guidance.
- `cli-tool-python` — Python package CLI with subcommands, config precedence, deterministic output, exit codes, packaging notes, and tests.
- `saas-dashboard` — authenticated dashboard, metrics, CRUD workflows, and admin views.
- `nextjs-app` — React/Next.js-style routes, components, state, and UI tests.
- `fastapi-backend` — Python API with routes, schemas, services, persistence boundaries, and tests.
- `cli-tool` — command-line product with subcommands, config, help text, and tests.
- `discord-bot` — bot commands, events, permissions, moderation/safety, and operations notes.
- `browser-extension` — manifest, popup/options UI, content scripts, storage, and tests.
- `data-app` — ingestion, transforms, charts, exports, fixtures, and validation checks.
- `internal-admin` — back-office workflows, roles, audit, approvals, and operational safeguards.

Example template-specific starts:

```bash
aider company new "Build a founder revenue dashboard with CSV import and cohort charts" \
  --template data-dashboard-streamlit \
  --name founder-metrics \
  --warehouse ~/AiderPlusWarehouse

aider company new "Build a Python CLI that exports reports from CSV files" \
  --template cli-tool-python \
  --name report-exporter \
  --warehouse ~/AiderPlusWarehouse

aider company new "Build a secure offline notes app with encrypted local files" \
  --template electron-desktop-app \
  --name secure-notes \
  --warehouse ~/AiderPlusWarehouse
```

---

## CLI reference

### Company commands

```bash
aider company templates
```

Print the rich template catalog, including descriptions, recommended skills, QA gates, post-create notes, and PRD seed prompts.

```bash
aider company create <idea> [--template TEMPLATE] [--name PROJECT_NAME] [--dry-plan] [-- AIDER_ARGS...]
```

Run the zero-to-MVP Company brief in the current repo. Aider arguments after
`--` are passed through to normal Aider startup.

```bash
aider company new <idea> [--template TEMPLATE] [--name PRODUCT_NAME] [--warehouse PATH] [--dry-plan] [-- AIDER_ARGS...]
```

Create or reuse a warehouse product repo, register it, scaffold the template, and
run the Company implementation loop inside that product repo.

```bash
aider company daemon --workflow PATH [--once] [--dry-run] [--status] [--run ISSUE_ID] [--departments LIST] [--max-iterations N] [--watch]
```

Run one issue-workflow daemon tick for an issue-backed workflow, preview it with
`--dry-run`, trigger one issue with `--run ISSUE_ID`, restrict the runner with
`--departments product,engineering,qa` and `--max-iterations N`, print
run/workspace status with `--status`, or add `--watch` to stream shared EventBus
progress while the run executes.

### Warehouse commands

```bash
aider warehouse init [PATH]
aider warehouse list [--warehouse PATH]
aider warehouse open PRODUCT [--warehouse PATH]
aider warehouse status [--warehouse PATH]
```

Initialize a warehouse, list registered products, print a product path, or show
registry/product/memory status.

### GUI and automation flags

```bash
aider --browser        # Streamlit browser GUI
aider --desktop        # native Tkinter desktop GUI
aider --desktop-tk     # alias for --desktop
aider --headless       # non-interactive/bot-friendly mode
aider --bot-mode       # alias for --headless
```

---

## Architecture

```text
User / CEO
  |
  v
CLI, Browser GUI, Desktop GUI, API/MCP, daemon, or chat adapter
  |
  v
Operations assistant loop
  |  - reads durable assistant session history
  |  - pulls assistant profile, project memory, skills, and warehouse context
  |  - decides whether to answer, clarify, remember, inspect, use tools, or delegate
  |
  +--> direct response / clarification / memory update / status brief
  |
  v
CompanyOrchestrator
  |
  +--> shared typed EventBus (lifecycle, daemon progress, COO actions, approvals)
  |       |
  |       +--> Browser GUI / Desktop GUI / Discord / daemon --watch / future API+MCP streams
  |
  v
Product -> UX -> Delivery -> Engineering -> Reviewer -> QA -> Delivery -> DevOps
  |
  v
Git-backed product repo + deliverables + audit log + approvals + memory
```

The orchestrator is the canonical product-building path. It publishes structured,
versioned runtime events through `aider.company.events.EventBus`, so surfaces
should send messages in and render shared EventBus results out rather than
reimplementing department, approval, lifecycle, audit, status, or deployment
behavior. `surface_messages.py` contains the shared formatting layer for these
events and the simple server-sent-event payload used by future API/MCP streams.
Each event carries `version: 1` and `severity` (`info`, `warning`, or `error`),
with explicit constants for supported/deprecated versions so future breaking
schema changes can be introduced with a compatibility window. The in-memory bus
keeps a bounded replay buffer and automatically prunes old events during
long-running sessions.

---

## Departments and workflow behavior

- **COO** receives user/channel messages, consults durable session history and
  project/warehouse memory, chooses direct response vs. routing, retries failed
  routing decisions, emits status/error events, and can escalate to humans.
- **Product** turns requests into requirements, PRDs, launch criteria, and
  structured clarification requests when ambiguity blocks execution.
- **UX** produces structured design specs with flows, states, accessibility,
  interaction details, and schema-gated validation/retry behavior.
- **Engineering** receives PRD and design context, plans implementation, uses the
  Aider coder loop, and can iterate with reviewer feedback.
- **Reviewer** checks implementation quality, context handoffs, risks, and
  metrics; reviewer comments can be injected into programmer revisions.
- **QA** writes validation plans, can run allowed test commands, handles feedback
  rerouting, and reports release confidence/regression risks.
- **DevOps** executes validated releases after Delivery readiness approval: it
  validates `DeliveryHandover`, runs allowlisted build/package/deploy commands,
  emits build/deploy lifecycle events, records rollback/deployment metadata, and
  refuses handoffs that still have critical blockers.

### DevOps release execution

Delivery remains the release-readiness gate, while DevOps is the execution layer
for a green handoff:

- `BuildArtifact`, `DeploymentTarget`, `DeploymentResult`, and
  `DeliveryHandover.from_dict()` provide structured release schemas so
  build/deploy artifacts can round-trip through department payloads, metadata,
  dashboards, and audit surfaces. Deployment results now include the selected
  provider/environment/config target, deployed URL, logs URL, human-readable
  `deployment_notes`, `deployed_at` timestamp, and a recorded rollback command
  when one can be generated safely.
- `DevOpsDepartment` validates the Delivery handover, performs builds, selects a
  provider target, performs deployments, emits `devops_build_started`,
  `devops_build_success`, `devops_deploy_started`, `devops_deployed`, and
  `devops_failure` lifecycle events, and returns rich build/deploy metadata in
  `deploy_report` deliverables.
- Safe shell execution uses explicit allowlists for Docker builds, Python package
  builds, npm builds, wheel creation, and tagging. Provider deploy commands for
  Vercel, Railway, Fly.io, AWS, and Docker Compose are generated from structured
  `DeploymentTarget.config` values instead of free-form shell snippets. Every
  provider command is still treated as high risk and requires explicit approval
  flags such as `devops_high_risk_approved`/`deploy_approved` before execution.
  Environment-specific approval signals such as `deployment_approvals.staging`,
  `devops_production_approved`, or `devops_critical_approved` can be used with
  target config `approval_level` values like `standard` or `critical`.
- Build detection can infer Docker, Python package, npm, Make, Cargo, or Go build
  commands from repository files, while configured deployment commands remain
  supported for real environments under the same allowlist/approval gates. When
  no external deployment command is configured, DevOps writes a local deployment
  manifest under `.aider/company/deployments/`.
- Deployment providers can be restricted with
  `AIDER_DEVOPS_DEPLOYMENT_PROVIDERS=local,vercel,railway,fly,aws,docker-compose`.
  Log capture defaults to `.aider/company/build-logs` and can be redirected with
  `AIDER_DEVOPS_LOG_CAPTURE_DIR`; set
  `AIDER_DEVOPS_ARTIFACT_UPLOAD_TARGET=s3://bucket/prefix` or
  `github://owner/repo/releases/tag` to surface durable log artifact URLs in the
  deployment result.
- Example deployment target payloads:

  ```json
  {
    "deployment_target": {
      "provider": "vercel",
      "environment": "production",
      "config": {"project": "invite-flow", "scope": "acme"}
    },
    "devops_high_risk_approved": true
  }
  ```

  ```json
  {
    "deployment_target": {
      "provider": "docker-compose",
      "environment": "staging",
      "config": {"file": "compose.yaml", "service": "web"}
    },
    "deploy_approved": true
  }
  ```

  Environment-specific config can override provider settings and approval level:

  ```json
  {
    "deployment_target": {
      "provider": "railway",
      "environment": "staging",
      "config": {
        "service": "web",
        "approval_level": "critical",
        "environments": {
          "staging": {"service": "staging-web", "approval_level": "standard"},
          "production": {"service": "prod-web", "approval_level": "critical"}
        }
      }
    },
    "deployment_approvals": {"staging": true},
    "deployment_notes": "Deploy staging release candidate for smoke testing."
  }
  ```
- The orchestrator routes validated release approvals through
  `_execute_devops_release()` and passes Delivery's `DeploymentTarget` into
  Delivery-originated DevOps handoff tasks so the lifecycle remains Engineering →
  QA → Delivery → DevOps.
- Company dashboard/status output includes the latest build artifact, provider
  badge text, deployment status, deployed URL, logs URL, deployment notes,
  **Last Deployed** timestamp, and a Rollback button that copies the recorded
  rollback command for human review instead of running it automatically.
- DevOps coverage includes successful build/deploy execution, provider-specific
  command generation, disabled-provider blocking, readiness-gate blocking,
  high-risk command gating, log URL generation, environment-specific config
  merging, mocked Vercel end-to-end deployment, and schema round-tripping tests.

Company state includes lifecycle phases, approval gates, resolved task IDs,
pending tasks, recovered gates, audit events, playbook memory, and project
outcomes.

---

## Browser and desktop GUIs

### Browser GUI

```bash
aider --browser
```

The Streamlit browser GUI exposes direct Aider chat, Company workflow controls,
Company dashboard data, approvals, audit logs, project memory, settings, and guide
pages.

### Desktop GUI

```bash
aider --desktop
```

The native desktop app uses Tkinter only. It exposes Company workflow controls,
background execution handling, approvals, audit/status panels, settings, and
per-agent chat paths without requiring Streamlit or a browser.

### Chat targets

- **Direct Aider** — classic Git-aware pair-programming.
- **Company Workflow** — COO-led Product → UX → Engineering → Reviewer → QA → DevOps.
- **COO** — briefing, status, memory, clarifications, routing, delegation, and error recovery.
- **Product** — requirements, ambiguity checks, PRDs, and launch criteria.
- **UX** — flows, states, accessibility, interaction details, and design specs.
- **Engineering** — implementation plans and code changes.
- **Reviewer** — implementation review and quality checks.
- **QA** — test plans, validation, release confidence, and regression guidance.
- **DevOps** — validated build/package/tag/deploy execution, release metadata,
  deployment status, rollback, recovery, and MCP/ops guidance.

---

## Settings and configuration

Browser and desktop settings use shared helpers and are organized around:

1. **Global Aider defaults** — main, weak, and editor model defaults for direct chat.
2. **Per-agent overrides** — model, prompt caching, API key override, and local endpoint/setting for COO, Product, UX, Engineering, Reviewer, QA, and DevOps.
3. **Provider and integration secrets** — OpenAI, Anthropic, OpenRouter, Discord bot token, and related credentials.
4. **Advanced files** — `.env` lines and raw `.aider.conf.yml` editing.

Useful environment overrides:

```bash
AIDER_COMPANY_AGENT_MODELS="coo=gpt-5.5,product=gpt-5.5,engineering=gpt-5.5"
AIDER_COMPANY_MODEL_COO=gpt-5.5
AIDER_COMPANY_MODEL_ENGINEERING=claude-sonnet-4-5
AIDER_COMPANY_AGENT_CACHING="product:true,ux:true,qa:false"
AIDER_COMPANY_CACHING_COO=true
AIDER_COMPANY_CACHING_ENGINEERING=true
AIDER_MCP_ENABLED=true
DISCORD_BOT_TOKEN=your-discord-bot-token
```

Use GUI preview before saving, then apply/restart Company sessions so new agent
loops pick up model, key, caching, `.env`, and `.aider.conf.yml` changes.

---

## Memory, playbooks, skills, and self-improvement

Aider Plus keeps learning artifacts local and inspectable:

- Product repos keep project memory, conversation summaries, audit data,
  playbook patterns, skill usage, approval state, and recent outcomes.
- Warehouses keep shared COO memory under `.aider/coo/` for cross-product context.
- Retrieval pulls relevant memory into department context before work is executed.
- Audit pattern extraction can turn repeated outcomes into playbook guidance.
- Procedural skills live under `.aider/skills/<scope>/<name>/SKILL.md` where
  scopes include `shared`, `coo`, `product`, `ux`, `engineering`, `reviewer`,
  `qa`, and `devops`.
- Skill retrieval scores skills against the task and role; browser and desktop
  dashboards show available/recently used skills with local `SKILL.md` paths, and
  the COO can answer skill questions through `inspect_skills()`.
- Self-improvement is additive: learned procedural workflows become
  approval-gated JSON proposals under `.aider/skill_proposals/` before they are
  installed as skills.

---

## MCP integration

Aider Plus includes an optional MCP layer under `aider/mcp/`:

- configuration parsing and enable/disable flags;
- manager lifecycle for connected tools;
- adapters that expose tool calls to department agent loops;
- approval handlers so sensitive MCP requests can appear in the same approval
  surfaces as Company gates.

MCP should be treated as an operations surface. Keep approval gates enabled for
filesystem, network, deployment, ticketing, or production-adjacent tools.

---

## Discord and other chat adapters

Discord is intentionally a thin adapter. It can accept chat text, associate it
with a session, and forward it to headless Aider or Company Mode, but it should
not own dashboards, lifecycle buttons, approval gates, audit logs, COO status
semantics, or product workflow behavior.

This makes Discord replaceable with Slack, Matrix, webhooks, queues, or custom
chat apps: normalize message/channel/session identity, then call the shared
runtime.

Install the optional dependency when running the Discord adapter directly:

```bash
python -m pip install discord.py
```

---

## Company daemon workflows

The Company daemon is for issue-backed, review-gated automation. A workflow file
uses YAML front matter plus a prompt body. The daemon can:

- read candidate issues from a local JSON tracker;
- filter by labels/status;
- prepare per-issue Git workspaces;
- enforce max concurrent agents, turns, attempts, and hook timeouts;
- run hooks such as `after_create`, `before_run`, `after_run`, and `before_remove`;
- render issue placeholders into the Company prompt;
- use the built-in `CompanyDaemonRunner` by default to execute Company Mode cycles across Product/UX, Engineering review, QA, Delivery, and DevOps departments when they are registered;
- capture changed files, concise diff summaries, JSON-stored diffs, recent commit messages, QA checks, review feedback, Delivery handover data, DevOps build/deploy status, links, risks, completed stages, failed stages, and partial-success state as structured proof-of-work;
- continue through later departments when a stage fails where possible, marking `partial_success: true` for recoverable partial runs;
- emit `daemon_run_progress` lifecycle events as stages start/finish so live surfaces can show long-running progress;
- produce `.aider/company/run-state.json`, `.aider/company/proof-of-work.json`, and a human-readable `.aider/company/proof-of-work.md` with concise diff summaries;
- expose `CompanyDaemon.get_status()` with running/idle state, last run, active
  workflows, pending proof-of-work, recent proof artifacts, hook timeout, and
  max-workspace safety limits for dashboards and COO inspection;
- comment, attach PR URLs, and transition tracker state.

Example workflow:

```markdown
---
tracker:
  kind: local
  path: ./issues.json
  labels: [ready]
workspace:
  root: ./.aider/company-runs
  clean: false
agent:
  max_concurrent_agents: 1
  max_turns: 3
  max_attempts: 2
company:
  route: product_to_release
  require_release_approval: true
hooks:
  before_run: python -m pytest -q tests/company
  timeout_seconds: 120
---
Implement the issue as a reviewable Aider Plus Company Mode change.

Issue: {{issue.identifier}}
Title: {{issue.title}}
Description: {{issue.description}}
URL: {{issue.url}}
```

Run it:

```bash
aider company daemon --workflow .aider/company/workflow.md --dry-run
aider company daemon --workflow .aider/company/workflow.md --once
aider company daemon --workflow .aider/company/workflow.md --run ISSUE-123
aider company daemon --workflow .aider/company/workflow.md --run ISSUE-123 --departments engineering,qa --max-iterations 2
aider company daemon --workflow .aider/company/workflow.md --status
```

If a repo has `AIDER_WORKFLOW.md` at its root, the browser and native desktop
System Overview panels also show daemon status, last run, active workflows,
pending proof-of-work, recent daemon runs, and recent proof artifact paths/summaries. The COO uses
`list_daemon_workflows()` to answer the same questions from chat/status surfaces.

---

## Model/provider updates in this fork

This repository also tracks selected upstream-style model and provider updates,
including Claude 4 temperature/thinking-token handling, Claude Opus 4.7 settings
for Bedrock/Vertex/OpenRouter, GPT-5.5 model settings, updated model alias tests,
and documentation/website examples that reflect those model capabilities.

---

## Development

Useful checks:

```bash
python -m py_compile aider/company/templates.py aider/company/warehouse.py aider/company/cli.py aider/company/daemon/__init__.py aider/company/daemon/runner.py aider/company/workflow.py aider/company/tracker.py aider/main.py
python -m pytest tests/company/test_warehouse_cli.py tests/company/test_zero_to_mvp_cli.py tests/company/test_delivery_department.py
```

Important files:

- `aider/company/templates.py` — zero-to-MVP prompts and template starter structures.
- `aider/company/warehouse.py` — thin warehouse registry for Git-backed product repos.
- `aider/company/cli.py` — `aider company ...` and `aider warehouse ...` parsing/handlers.
- `aider/company/coo.py` — operations-assistant sessions, action decisions, memory, status, bus, retry, and delegation.
- `aider/company/orchestrator.py` — workflow, approvals, lifecycle, context, handoffs, and audit coordination.
- `aider/company/schemas/` — structured PRD, design, Delivery handoff, build artifact, and deployment result contracts.
- `aider/company/departments/` — Product, UX, Engineering, Reviewer, QA, Delivery, and DevOps implementations.
- `aider/company/daemon/` — issue daemon, built-in runner, run state, proof-of-work, and workspace handling.
- `aider/company/workflow.py` — daemon workflow file parsing, hooks, and prompt rendering.
- `aider/company/tracker.py` — tracker abstraction and local JSON tracker adapter.
- `aider/company/skills.py` — role-scoped skill retrieval, usage tracking, and proposal approval.
- `aider/company/self_improvement.py` — skill proposal generation from successful patterns.
- `aider/company/surface_messages.py` — shared lifecycle/status/approval/audit message formatting.
- `aider/memory/` — conversation memory, project memory, retrieval, consolidation, and pattern extraction.
- `aider/mcp/` — MCP configuration, manager, adapters, and server helpers.
- `aider/gui.py` — Streamlit browser GUI.
- `aider/desktop.py` — Tkinter desktop GUI.
- `aider/integrations/discord.py` — Discord chat adapter.
- `tests/company/`, `tests/mcp/`, and focused `tests/basic/` files — workflow, COO, GUI settings, approvals, lifecycle, daemon, DevOps release execution, Discord, and MCP coverage.

---

## Safety note

Aider Plus is experimental software. Use Git branches, review generated changes,
keep approval gates enabled for important work, and treat MCP/deployment actions
as operations that need human oversight.

---

## Upstream Aider docs

Because this is a fork, upstream Aider docs remain relevant for baseline install,
usage, configuration, providers, model naming, and classic pair-programming
behavior:

- https://aider.chat/docs/install.html
- https://aider.chat/docs/usage.html
- https://aider.chat/docs/llms.html
- https://aider.chat/docs/config.html

## Aider Plus commit additions summary

This list summarizes non-merge, non-README-only Aider Plus commits in
chronological order. README-only refreshes are intentionally omitted, so the list
focuses on functional, test, integration, and non-README documentation changes.

- `79c45c3` — Disabled deprecated temperature handling for Claude 4 model calls.
- `39023f9` — Disabled temperature for Opus 4 models and gated `thinking_tokens`.
- `93dfacc` — Added Claude Opus 4.7 model settings for Bedrock, Vertex, and OpenRouter.
- `65cb4d3` — Reformatted the `thinking_tokens` model check for readability.
- `0189cf4` — Refreshed website/config documentation and sample analytics assets.
- `cd24a3a` — Updated model alias tests for Sonnet and Opus expectations.
- `308b154` — Added GPT-5.5 model settings across supported providers.
- `c723364` — Refreshed website/config documentation and sample analytics assets.
- `3ec8ec5` — Updated FAQ token percentages and switched the history model to GPT-5.5.
- `e56bd79` — Added headless mode and initial Discord integration scaffolding.
- `531da4b` — Completed headless/Discord support with CLI, coder, integration, and tests.
- `22f3b87` — Split agent-loop context construction into an explicit step.
- `79219d2` — Used coder-native message formatting to preserve agent context caching.
- `847d109` — Preserved Aider prompt caching metadata in agent context messages.
- `3a978a7` — Refactored agent-loop message assembly around coder-native APIs.
- `46f43ae` — Removed an obsolete `prepare_messages_for_llm` stub.
- `d121a2d` — Made the agent loop rely on coder-managed message formatting.
- `bd3c1a2` — Used coder message APIs in the agent loop when available.
- `d34e119` — Simplified agent-loop handling by routing messages through `coder.run`.
- `8883c2c` — Refined user-message handoff inside the agent loop.
- `af4656f` — Added architect/editor orchestration flow to the agent loop.
- `44a901d` — Added a minimal `ToolRegistry` for agent tool execution.
- `5979780` — Fixed malformed HTTP scraper `User-Agent` header handling.
- `4154d4b` — Added dream-consolidation support for Discord session memory.
- `edda09f` — Added initial Company orchestration code sketches.
- `5f80841` — Refactored Discord flow to submit `EngineeringDepartment` tasks.
- `5c14ef7` — Wired the Discord bot through `CompanyOrchestrator` scaffolding.
- `98cc4a0` — Added guided onboarding and first-run prompts.
- `01b0222` — Prompted for Discord bot token during onboarding.
- `e2c9ef7` — Added desktop app mode for the browser UI.
- `41382aa` — Improved desktop GUI defaults and lifecycle handling.
- `bf3a772` — Added an OpenRouter API key field to GUI settings.
- `19f31a7` — Fixed engineering department agent-loop handoff.
- `275507b` — Added Product Department and prototype Discord product flow.
- `bd6fbe9` — Routed PRD context through Company handoffs.
- `d461fde` — Added blocking PRD approval handoff before engineering work.
- `f867288` — Added a project state machine to the orchestrator.
- `fe4823d` — Persisted Company approval gates in project memory.
- `6014143` — Added per-department tool permissions and QA test execution.
- `53cec2a` — Added DevOps and UX stages to the delivery pipeline.
- `f98a6e5` — Routed release approvals to DevOps.
- `a8a55ed` — Added Company audit logging and a post-mortem playbook.
- `5a6191c` — Stabilized Company core interfaces for a v1-style architecture.
- `e1341b1` — Centralized lifecycle approvals and audit viewing.
- `782b1b9` — Stabilized Company workflow boundaries.
- `df324ea` — Hardened dependency checks and lint issues.
- `6c9b22b` — Applied small stability fixes.
- `63d117c` — Added Company workflow controls to the desktop GUI.
- `8dd7ff7` — Improved desktop Company background handling.
- `c697cae` — Improved desktop Company workflow UI.
- `f8b99c5` — Added an engineering reviewer phase loop.
- `054d1a8` — Enhanced engineering reviewer intelligence.
- `3685579` — Improved programmer revision feedback handling.
- `40289e7` — Injected reviewer feedback into programmer revisions.
- `1c6da07` — Added CLI approval handling and QA feedback rerouting.
- `6078c3d` — Added CLI approval and tool-permission tests.
- `f45c349` — Added memory retrieval and observability metrics.
- `a95cc75` — Added retrieval-aware playbook pattern extraction.
- `c6ac28c` — Added Company prompt caching controls.
- `1403bd0` — Added additional Company prompt caching controls/config paths.
- `f1219c2` — Added structured Product clarification workflow.
- `e451dc2` — Handled clarification approval responses.
- `c0d1e8a` — Added structured UX design specs.
- `6f9a4e8` — Integrated PRD and design specs into engineering prompts.
- `b2a2fcc` — Hardened engineering review context handoff.
- `5172ae9` — Hardened reviewer handoff and metrics collection.
- `e51ce8f` — Improved engineering review context handoff.
- `4eeb86c` — Polished engineering reviewer safeguards.
- `94fc2b1` — Refined engineering reviewer phase controls.
- `bdc5922` — Added a UX design schema gate.
- `3cdd37a` — Propagated UX schema-gate context through handoffs.
- `9640f2f` — Wired UX schema-gate retry flow.
- `cbbe387` — Added tests for UX schema-gate retry handling.
- `97b1cf7` — Hardened UX structured output handling.
- `dd574f2` — Refactored the desktop app to native Tkinter.
- `36b8061` — Added browser UI settings and an agent prompt box.
- `c627a7a` — Added COO status observability surfaces.
- `f3dc31e` — Improved Product PRD revision handling.
- `bc1faf2` — Added Company agent prompt caching configuration.
- `62b8428` — Added COO retry and error-routing resilience.
- `74b9559` — Hardened COO route-decision aliases.
- `af1d1b3` — Hardened COO retry escalation handling.
- `ff59e96` — Added browser and desktop settings editors.
- `0832ccc` — Added per-agent chat tabs and settings.
- `fbc97b3` — Documented desktop GUI tabs and fields.
- `7e3d44b` — Polished desktop guide placement and agent labels.
- `9595966` — Added the optional MCP integration layer.
- `be2873e` — Added an initial zero-to-MVP Company create flow.
- `9983101` — Extended the zero-to-MVP Company create flow.
- `c600ffe` — Added the product warehouse manager.
- `1e7d280` — Polished GUI settings and observability.
- `7f10796` — Aligned COO architecture and kept Discord as a thin adapter.
- `c5d43a1` — Added product warehouse scaffolding.
- `cf0c5ba` — Polished settings UI and added Discord token configuration.
- `1f0b86c` — Improved Company skill retrieval and dashboard visibility.
