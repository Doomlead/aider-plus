# Aider Plus

Aider Plus is a Git-native product studio built on top of Aider. It keeps the
classic Aider loop—real repositories, diffs, branches, tests, commits, and
reviewable changes—then adds Company Mode, an operations assistant, warehouse
product creation, local memory, skills, MCP tooling, GUI/chat surfaces, and an
issue-driven daemon.

Aider Plus is intentionally **not** a synthetic project runtime. Product work
still happens inside normal Git repositories; the extra layers coordinate agents,
approvals, memory, queues, and release evidence around that repo-native loop.

## Quickstart

### Install for local development

```bash
python -m pip install -e '.[browser]'
```

The `browser` extra enables the Streamlit UI. The desktop UI uses Tkinter from
the Python standard library.

### Run the guided Company setup

```bash
aider company init
```

The setup flow initializes a warehouse, chooses a default template, captures
optional GitHub Issues daemon settings, records preferred models and prompt
caching choices, validates available provider API keys, optionally enables MCP,
writes a tailored `.env.example`, and writes `AIDER_WORKFLOW.md`.

For the simplest first run, use the minimal onboarding preset. It asks for a warehouse, template, and one model, shows `Step N of M` progress, and detects defaults from environment variables such as `AIDER_MODEL`, provider API keys, `GITHUB_REPO`, and `AIDER_MCP_CONFIG`:

```bash
aider company init --warehouse ./AiderPlusWarehouse --template nextjs-saas --model gpt-5.5
```

For non-interactive or full setup, add `--advanced` to configure GitHub issue tracking, MCP, and per-department model overrides:

```bash
aider company init \
  --advanced \
  --warehouse ./AiderPlusWarehouse \
  --template nextjs-saas \
  --github-repo owner/repo \
  --model gpt-5.5 \
  --enable-mcp \
  --product-idea "Build my MVP" \
  --product-name my-mvp \
  --yes
```

On first run, Aider Plus offers this setup automatically. Use
`--skip-onboarding` when you want classic Aider without the Company quickstart
prompt.

### Run classic Aider when you just want pair programming

```bash
aider --model gpt-5.5
```

Run one non-interactive task for scripts, queues, service wrappers, chat workers,
or CI:

```bash
aider --headless --model gpt-5.5 --msg "Refactor the parser and add tests"
```

`--bot-mode` is an alias for `--headless`.

### Start Company Mode in the current repo

```bash
aider company create "Build a habit tracker with login, streaks, and a dashboard" \
  --template nextjs-saas \
  --name habit-tracker
```

Preview the generated Company brief without changing files:

```bash
aider company create "Build a Stripe webhook API" \
  --template fastapi-api \
  --dry-plan
```

### Browse templates

```bash
aider company templates
```

## Core concepts

- **COO / COO assistant** — the CEO-facing operations assistant. It answers,
  clarifies, remembers, inspects status, routes work, delegates to Company
  workflow, and escalates when humans are needed.
- **Company Mode** — a multi-department product workflow layered on top of
  normal Aider rather than a replacement runtime.
- **Department** — a role-specific agent loop. Current departments are Product,
  UX, Delivery, Engineering, Reviewer, QA, DevOps, AppSec, and PlatformSec.
- **Delivery** — the coordination and readiness owner. Delivery tracks
  milestones, blockers, validation confidence, and the handoff into DevOps.
- **DevOps** — the release execution layer. It validates Delivery handoffs, runs
  approved build/package/deploy steps across local, Vercel, Railway, Fly.io,
  AWS, Docker Compose, Netlify, Render, Cloudflare Pages, Kubernetes, and Helm
  targets, captures logs, and records deployment, dry-run preview, and rollback
  metadata.
- **Skills vs. playbooks** — skills are role-scoped `SKILL.md` procedures
  retrieved for a task; playbooks are reusable lessons extracted from prior
  successful work.
- **Memory Fabric** — local, inspectable project, conversation, COO, warehouse,
  and retrieval context used to make later runs less stateless.
- **EventBus** — the versioned in-process runtime stream for lifecycle,
  department, approval, daemon, COO, and deployment events.
- **Approval gate** — an explicit human decision point for risky actions such as
  deployments, recovery, skill proposals, or sensitive tool use.
- **ProofOfWork** — daemon-run evidence that records changed files, diffs,
  checks, review/QA feedback, handoffs, release status, retry counts, errors,
  and partial-success details.
- **Warehouse** — a registry of normal product Git repositories under
  `products/<slug>/`, plus shared COO memory. It is not a custom VCS.
- **Template** — a zero-to-MVP starter shape with recommended skills, starter
  files, QA gates, post-creation notes, and PRD prompt seeds.

## What Aider Plus adds to upstream Aider

- **Company Mode:** Product → UX → Delivery → Engineering → Reviewer → QA →
  Delivery → DevOps workflow for turning ideas, issues, or chat requests into
  implementation plans, code changes, validation notes, release execution, and
  audit trails.
- **Operations assistant:** a COO layer that can answer directly, ask clarifying
  questions, remember context, inspect project state, list daemon workflow state,
  route work to departments, report status, and surface queue/error telemetry.
- **Warehouse-backed product studio:** `aider company new` creates a named
  product repo inside a central warehouse, registers it, scaffolds starter files,
  and runs Company Mode against the new repo.
- **Local memory and skill learning:** scoped memory records, recall packets,
  reinforcement outcomes, health checks, skill evidence, and self-improvement
  proposal flows.
- **Code-intelligence graph:** symbol search, callers/callees, impact analysis,
  affected-test suggestions, route awareness, and graph-aware context. Company
  QA uses graph-suggested affected tests, Reviewer and Delivery consume impact
  analysis for review/release risk, and route detection covers decorator routes
  plus Next.js, Nuxt, SvelteKit, Django, Rails, Express, and NestJS patterns.
- **MCP and adapters:** MCP configuration plus Discord, Slack-compatible,
  Matrix, headless/bot, and GUI surfaces that reuse the same Company runtime
  contracts and shared surface-message rendering.
- **Issue-driven daemon:** tracker-backed workspaces, retries, proof-of-work,
  security scans, hooks, release metadata, and orchestrator-owned department
  sequencing for unattended or queued work.

## Repository map

- `.github/` — issue templates and CI/release workflows.
- `aider/` — primary Python package: CLI, coder loop, Company orchestration,
  memory, MCP, integrations, GUI/desktop/workspace, resources, and query packs.
- `benchmark/` — benchmark runners, analysis helpers, and SWE-bench support.
- `docker/` — container build definitions.
- `docs/` — architecture, Company, operations, and contributor reference docs.
- `requirements/` — dependency source/lock files split by feature area.
- `scripts/` — maintenance, release, docs, metadata, and benchmark helpers.
- `tests/` — regression/unit/integration coverage for CLI, coders, Company
  orchestration, memory, MCP, integrations, daemon, and workspace paths.

## Start here if you want to change X

| Change area | Start here | Then inspect | Focused tests |
| --- | --- | --- | --- |
| Classic Aider CLI/coding loop | `aider/main.py` | `aider/coders/`, `aider/repo.py`, `aider/run_cmd.py` | `tests/basic/`, root `tests/test_*.py` |
| Company command parsing | `aider/company/cli.py` | `aider/main.py`, `aider/company/runtime.py` | `tests/company/test_zero_to_mvp_cli.py`, `tests/company/test_warehouse_cli.py` |
| A Company department capability | `aider/company/departments/` | `aider/company/orchestrator.py`, `aider/company/schemas/` | `tests/company/test_*_department.py`, `tests/company/test_e2e_pipeline.py` |
| COO assistant behavior | `aider/company/coo.py` | `aider/company/surface_messages.py`, `aider/company/events.py` | `tests/company/test_coo_agent_framework.py` |
| Daemon or tracker workflow | `aider/company/daemon/` | `aider/company/workflow.py`, `aider/company/tracker.py` | `tests/company/test_symphony_daemon.py`, `tests/company/test_tracker_adapters.py` |
| DevOps release handoff | `aider/company/departments/devops.py` | `aider/company/schemas/`, `aider/company/orchestrator.py` | `tests/company/test_release_deployment.py`, `tests/company/test_devops_department.py` |
| Memory/skill retrieval | `aider/memory/` | `aider/company/skills.py`, `aider/company/self_improvement.py` | `tests/memory/`, `tests/company/test_memory_*.py` |
| MCP tooling | `aider/mcp/` | `docs/company/mcp.md` | `tests/mcp/test_mcp_integration.py` |
| Browser/desktop/chat surfaces | `aider/gui.py`, `aider/desktop.py` | `aider/integrations/discord.py`, `aider/integrations/slack.py`, `aider/integrations/matrix.py`, `aider/company/events.py`, `aider/company/surface_messages.py` | `tests/test_desktop_*.py`, `tests/company/test_discord_lifecycle.py`, `tests/integrations/test_thin_adapters.py` |
| Code graph | `aider/codegraph/` | `aider/company/context.py`, `aider/company/departments/qa.py`, `aider/company/departments/engineering.py`, `aider/company/departments/delivery.py` | `tests/codegraph/test_codegraph.py`, `tests/company/test_codegraph_integrations.py` |

## Contributor paths

- Start with the [First Code Tour](docs/architecture/first-code-tour.md) for a
  guided walk from `aider/main.py` through the Coder, COO assistant,
  `CompanyOrchestrator`, departments, shared services, daemon workspaces,
  templates, warehouse repos, and DevOps release path.
- Use the [New Contributor Playbook](docs/contributing/new-contributor-playbook.md)
  for a first bug-fix walkthrough, department capability guide, new
  `aider company ...` command checklist, tests for orchestrator/department
  flows, and a command-to-module/test map.
- Follow [CONTRIBUTING.md](CONTRIBUTING.md) for setup, coding standards, test
  commands, and pull request expectations.

## Deeper reference docs

- [First Code Tour](docs/architecture/first-code-tour.md) — architecture skeleton
  for contributors.
- [Memory Fabric](docs/architecture/memory-fabric.md) — scoped memory,
  visibility rules, evidence-backed records, skill promotion, recall policy,
  migrations, health, compaction, and hardening.
- [COO architecture](docs/company/nanobot_coo_architecture.md) — assistant
  routing, status, memory, escalation, and workflow integration.
- [Zero-to-MVP templates](docs/company/zero_to_mvp.md) — template registry,
  product creation, starter files, and warehouse-backed repos.
- [Symphony daemon](docs/company/symphony_daemon.md) — issue daemon, workflows,
  workspaces, hooks, proof-of-work, retries, and security scans.
- [DevOps release execution](docs/company/devops-release.md) — Delivery → DevOps
  handoffs, approvals, build/package/deploy execution, rollback metadata, and
  release tests.
- [GUI and chat adapters](docs/company/gui-and-adapters.md) — browser GUI,
  desktop GUI, chat targets, Discord, and headless/bot-mode surfaces.
- [MCP integration](docs/company/mcp.md) — MCP configuration, manager, adapters,
  and Company/runtime integration.

## Development quick checks

```bash
python -m py_compile aider/company/templates.py aider/company/warehouse.py aider/company/cli.py aider/company/daemon/__init__.py aider/company/daemon/runner.py aider/company/workflow.py aider/company/tracker.py aider/main.py
python -m pytest tests/company/test_warehouse_cli.py tests/company/test_zero_to_mvp_cli.py tests/company/test_delivery_department.py
```

Run the memory regression safety net when changing retrieval, ranking,
compaction, promotion, or context injection:

```bash
pytest tests/memory tests/company/test_context_memory_fabric.py
```

Run `tests/company/test_release_deployment.py` when changing Delivery → DevOps
handoffs, build detection, deployment provider commands, high-risk approval
gates, artifact metadata, or rollback handling.

## Model/provider updates in this fork

This repository also tracks selected upstream-style model and provider updates,
including Claude 4 temperature/thinking-token handling, Claude Opus 4.7 settings
for Bedrock/Vertex/OpenRouter, GPT-5.5 model settings, model alias tests, and
website examples that reflect those model capabilities.

## Safety note

Aider Plus is experimental software. Use Git branches, review generated changes,
keep approval gates enabled for important work, and treat MCP/deployment actions
as operations that need human oversight.

## Upstream Aider docs

Because this is a fork, upstream Aider docs remain relevant for baseline install,
usage, configuration, providers, model naming, and classic pair-programming
behavior:

- https://aider.chat/docs/install.html
- https://aider.chat/docs/usage.html
- https://aider.chat/docs/llms.html
- https://aider.chat/docs/config.html
