<p align="center">
  <a href="https://aider.chat/"><img src="https://aider.chat/assets/logo.svg" alt="Aider Logo" width="280"></a>
</p>

<h1 align="center">Aider Plus</h1>

<p align="center">
<strong>Aider Plus</strong> is an agent-first fork of <a href="https://github.com/Aider-AI/aider">aider-chat</a>. It keeps Aider's git-aware pair-programming engine and adds headless automation, an autonomous tool-calling runtime, Nanobot-inspired COO routing, Product → UX → Engineering → QA → DevOps delivery workflows, structured PRD and design handoffs, schema gates, reviewer loops, human approvals, Discord/browser/native desktop surfaces, per-agent settings, persistent project memory, retrieval-aware learning, prompt-caching controls, audit logs, and operational dashboards.
</p>

---

## Table of contents

- [What Aider Plus is](#what-aider-plus-is)
- [What this repo now does](#what-this-repo-now-does)
- [Architecture overview](#architecture-overview)
- [Quickstart](#quickstart)
- [Installation](#installation)
- [Configuration and model providers](#configuration-and-model-providers)
- [Common workflows](#common-workflows)
- [Zero-to-MVP golden path](#zero-to-mvp-golden-path)
- [Products warehouse](#products-warehouse)
- [Company workflow](#company-workflow)
- [Nanobot COO orchestration](#nanobot-coo-orchestration)
- [Memory, retrieval, learning, and observability](#memory-retrieval-learning-and-observability)
- [Prompt caching and per-agent configuration](#prompt-caching-and-per-agent-configuration)
- [MCP integration](#mcp-integration)
- [Discord integration](#discord-integration)
- [Browser GUI](#browser-gui)
- [Native desktop mode](#native-desktop-mode)
- [Model, docs, benchmark, and website assets](#model-docs-benchmark-and-website-assets)
- [Development](#development)
- [Safety model](#safety-model)
- [Roadmap direction](#roadmap-direction)
- [Upstream Aider docs](#upstream-aider-docs)

---

## What Aider Plus is

Aider Plus turns upstream Aider into a foundation for **agentic software delivery**:

1. Gather repository state, repo maps, chat history, repo-scoped project memory, user instructions, workflow state, model metadata, retrieval-ranked artifacts, prior playbook lessons, and per-agent runtime configuration.
2. Decide whether to answer directly, run a headless Aider task, call agent tools, ask for clarification, route through the COO, or execute a structured Company workflow.
3. Use Aider's coder implementations for real edits, diffs, lint/test hooks, commits, repo-map context, prompt caching, provider-aware prompting, and git-native reviewability.
4. Coordinate larger work through Product, UX, Engineering, reviewer, QA, DevOps, approval gates, retry policies, audit logs, bounded revision loops, post-mortems, and persistent task status.
5. Learn from prior sessions by extracting typed audit patterns, deduplicating and bounding a playbook, retrieving only relevant lessons, and tracking task/QA/token/cache metrics across runs.
6. Expose the same runtime to CLI commands, scripts, Discord bots, Streamlit/browser UI, a zero-dependency Tkinter desktop app, and Python APIs.

In short: this repository is both an interactive AI pair-programmer and an embeddable runtime for autonomous software agents that can plan, implement, review, approve, test, release, observe, recover, and improve from historical outcomes. The headline differentiator is **ChatDev-like product creation with Aider-like continuous development**: a software company that keeps working after v0.

---

## What this repo now does

Aider Plus now combines upstream Aider with a multi-surface autonomous delivery runtime. In practical terms, this checkout can:

### Preserve the upstream Aider coding experience

- Run classic interactive Aider chat against selected files, repo maps, git state, lint/test commands, URL content, images, voice/watch/copy-paste flows, and Aider's edit formats.
- Use upstream coder modes including ask, code, architect, editblock, whole-file, unified-diff, diff-fenced, and patch-style editing.
- Keep git-native reviewability through tracked diffs, optional auto-commits, commit messages, dirty-file checks, `.aiderignore`, read-only context, and no-git workflows.
- Carry upstream documentation, website, benchmark, leaderboard, history, model-alias, requirements, and constraint assets forward in this fork.

### Support current model/provider metadata

- Ship refreshed model settings and aliases for OpenAI GPT-5.x/GPT-5 Codex/GPT-5.5/GPT-5 Pro, Claude Sonnet/Opus 4.x, Gemini 2.5/3, DeepSeek, OpenRouter, Bedrock, Vertex, and related variants.
- Handle provider-specific behavior such as Claude 4 temperature disabling, `thinking_tokens` gating, reasoning-effort defaults, overeager prompting, OpenRouter aliases, Bedrock/Vertex entries, and updated model-test expectations.
- Expose OpenRouter setup prominently through onboarding plus browser and desktop settings panels.

### Run as a headless or embeddable agent

- `--headless` and `--bot-mode` configure non-interactive defaults for queues, scripts, CI, Discord workers, and service wrappers by disabling rich/streaming terminal UI and auto-approving prompts.
- `AiderAgentLoop` builds structured context from coder state, repo information, recent conversation, project memory, prior coder results, and project instructions.
- Agent calls can use LiteLLM-compatible tool definitions, bounded tool iterations, prompt-cache metadata, coder-native message assembly, and structured result metadata.
- Coding tool calls execute through Aider-backed architect/editor orchestration so planning and implementation can be separated while still using normal Aider file editing.
- `ToolRegistry` centralizes tool registration, tool dispatch, department-aware allowlists, and structured permission failures.
- Optional MCP client support lazily connects project/task-scoped stdio, Streamable HTTP, or SSE servers, adapts discovered MCP tools into `ToolRegistry`, and keeps LiteLLM/LiteLLM Proxy as the model gateway rather than a tool transport.

### Orchestrate Company Mode delivery

- `CompanyOrchestrator` routes work through a software-company workflow with project lifecycle state, department registration, handoffs, approval gates, background task management, audit events, status/dashboard data, and post-mortem learning.
- Product performs LLM ambiguity detection, creates CEO clarification gates with targeted questions, resumes PRD drafting from human answers, generates typed PRDs, self-reviews quality, and handles PRD revision requests.
- UX produces schema-validated `DesignSpecV2` handoffs from Product context, including screens, components, data contracts, interaction states, accessibility, state management, and error handling.
- The schema gate validates UX deliverables before Engineering receives them, retries once with actionable rejection feedback, and returns a `validation_failed` deliverable if the design remains incomplete or inconsistent.
- Engineering implements from PRD/design/schema-gate/playbook context using programmer/reviewer sub-phases, bounded revision prompts, structured reviewer feedback, and reviewer safeguard metrics.
- QA runs checks, records pass/fail/no-test outcomes, and can route structured feedback back to Engineering.
- Release approval can block deployment; DevOps records deployment or release completion.
- Post-mortem learning extracts patterns from outcomes and adds only novel, bounded, typed lessons to future playbooks.

### Coordinate through a Nanobot-inspired COO

- `NanobotCOO` treats the human as the CEO and the COO as a persistent personal assistant plus company operator.
- The COO feature list is intentionally Nanobot-like: a small readable core, research-friendly decision objects, built-in chat/API/memory/MCP/deployment paths, and hackable adapters instead of a hidden monolith.
- Chat messages arrive from CLI, Discord, browser, desktop, or API adapters; the machine-learning agent loop decides whether tools are needed; memory and skills are pulled into context; product-building or iteration requests are forwarded to `CompanyOrchestrator`.
- `COOActionDecision` lets the COO answer directly, ask CEO clarification, inspect status, remember/recall COO memory, reserve tool use, or delegate to the internal company.
- Delegation still uses `COORouteDecision` and the existing CompanyOrchestrator path, so Product → UX → Engineering → QA → DevOps logic remains unchanged.
- Routing is resilient: transient failures are retried, bad route aliases are normalized, final failures can fall back to a safe department, and exhausted retries create observable human-escalation metadata.
- Browser and desktop are the full operational consoles for approvals, audit/status views, memory, settings, and dashboards; Discord is kept as a chat-app ingress/egress adapter that forwards messages into the shared COO/company runtime.

### Expose multiple user surfaces

- **Terminal**: direct Aider CLI, `--headless`, `--bot-mode`, `--desktop`, onboarding commands, and approval commands.
- **Discord**: chat-app adapter only; it receives/sends chat messages and delegates all product, approval, audit, status, memory, and dashboard behavior to the shared COO/company runtime exposed in desktop/browser/API surfaces.
- **Browser GUI**: Streamlit chat UI with main Chat, Company dashboard, Memory, Settings, Guide, and per-agent tabs for COO/Product/UX/Engineering/Reviewer/QA/DevOps.
- **Native desktop GUI**: zero-dependency Tkinter launcher with direct chat, Company workflow routing, per-agent tabs, dashboard/status panels, approvals, audit log, project memory, settings editor, and guide text without requiring Streamlit, pywebview, WebView2, or a browser.
- **Python APIs**: `AiderAgentLoop`, `AgentLoopConfig`, `ToolRegistry`, `CompanyConfig`, `DepartmentConfig`/`AgentConfig`, `CompanyOrchestrator`, `NanobotCOO`, department classes, `ProjectMemory`, `ContextBuilder`, `MemoryRetriever`, `PlaybookManager`, `AuditPatternExtractor`, Discord/session helpers, settings helpers, and desktop/browser helpers.

---

## Architecture overview

Important runtime areas:

- **Aider core**: `aider/` contains the upstream coding engine, coders, model metadata, CLI, commands, repo map, IO, git integration, browser GUI, desktop launcher, settings helpers, and supporting utilities.
- **Agent loop**: `aider/agent/` contains tool definitions, the department-aware `ToolRegistry`, prompt-cache-aware agent calls, structured reviewer calls, and the Aider-backed agent loop.
- **Company workflow**: `aider/company/` contains the orchestrator, COO router, state manager, lifecycle transitions, departments, approval gates, department/agent configuration, context builder, audit helpers, schemas, UX schema validators, playbook manager, and agent-loop factory.
- **Memory**: `aider/memory/` contains conversation memory, project memory, dream consolidation, repository memory, the TF-IDF retriever, and audit pattern extraction.
- **Integrations**: `aider/integrations/` contains the Discord adapter.
- **GUI and desktop**: `aider/gui.py`, `aider/desktop.py`, and `aider/settings.py` expose direct chat, Company Mode, per-agent tabs, and settings through browser and native desktop paths.
- **Tests**: `tests/company/` contains focused coverage for company workflow, permissions, prompt caching, playbook/pattern extraction, UX schema gates, Discord lifecycle, engineering review, COO routing/resilience, settings helpers, and desktop label behavior.
- **Docs/site/benchmarks**: `aider/website/`, `benchmark/`, root docs, histories, and requirement files retain upstream assets plus Aider Plus updates.

High-level flow:

```text
User / Script / chat app adapters / Browser GUI / Native Desktop
        |
        +--> classic Aider coder path
        |
        +--> Headless AiderAgentLoop
        |       |
        |       +--> ToolRegistry --(authorization)--> aider_coder
        |       |
        |       +--> Aider coder modes, repo map, git, lint/test hooks
        |       |
        |       +--> optional cache_control metadata + structured reviewer calls
        |
        +--> NanobotCOO
        |       |
        |       +--> per-session message bus + persisted session history
        |       +--> deterministic or LLM route decision
        |       +--> retry/fallback/human-escalation metadata
        |       +--> CompanyOrchestrator department task
        |
        +--> CompanyOrchestrator
                |
                +--> CompanyConfig / AgentConfig / DepartmentConfig
                +--> Product -> optional clarification approval -> PRD approval
                +--> UX -> DesignSpecV2 -> schema gate -> optional retry
                +--> Engineering programmer/reviewer loop with PRD/design/gate context
                +--> QA -> pass/fail/no-test metrics or Engineering reroute
                +--> Release approval -> DevOps
                +--> Audit log -> post-mortem -> pattern extraction
                +--> PlaybookManager -> retrieval-ranked future context
                +--> CompanyStateManager -> memory + metrics + cache stats persistence
```

---

## Quickstart

### Install and run normally

```bash
python -m pip install -e '.[browser]'
aider --model gpt-5.5
```

### Run one headless task

```bash
aider --headless --model gpt-5.5 --msg "Refactor the parser and add tests"
```

`--headless` also works as `--bot-mode` and is intended for scripts, queues, CI jobs, Discord workers, and service wrappers.

### Start the browser GUI

```bash
aider --browser
```

### Start the native desktop app

```bash
aider --desktop
```

### Start onboarding

```bash
aider onboard
# or
aider init
```

### Resolve a pending approval from the terminal

```bash
aider approve <gate-id>
aider reject <gate-id> "Needs a smaller scope before release"
```

Run those approval commands from the repository root that contains `.aider/project_memory.json`.

### Run the optional MCP server façade

```bash
python -m pip install -e '.[mcp]'
aider-plus-mcp
```

The MCP server entry point exposes safe status, memory/context, Company-task, headless-task, and approval actions to MCP-aware clients while keeping normal model routing in LiteLLM/Aider.

---

## Installation

For development or local use from this checkout:

```bash
python -m pip install -e .
```

For browser work, install browser extras:

```bash
python -m pip install -e '.[browser]'
```

For test/development workflows:

```bash
python -m pip install -e '.[dev,browser]'
```

Optional integration dependencies depend on the surface you use:

- Discord bot support requires the Discord Python dependencies expected by `aider/integrations/discord.py`.
- MCP support is optional; install it with `python -m pip install -e '.[mcp]'` and use the `aider-plus-mcp` console script when you want Aider Plus to expose its safe MCP server façade.
- Desktop mode uses Tkinter and the Python standard library for its windowing layer; install your OS package for Tkinter if your Python distribution omits it.
- Browser mode uses Streamlit and browser extras.
- Model providers require the relevant API keys in environment variables, `.env`, `.aider.conf.yml`, onboarding output, or GUI settings.
- Python 3.14 support is marked experimental in this branch.

---

## Configuration and model providers

Aider Plus inherits upstream Aider configuration behavior:

- CLI flags, `.aider.conf.yml`, environment variables, and model/provider settings continue to work.
- Model settings are defined in `aider/resources/model-settings.yml` and provider handling is in `aider/models.py`.
- OpenRouter support is emphasized in onboarding plus browser and desktop settings.
- This branch includes model metadata additions for GPT-5.x/GPT-5.5, Claude 4.x, Gemini 2.5/3, and newer provider variants.
- Advanced model settings docs and sample analytics assets are refreshed in the bundled website source.

Typical provider setup:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export OPENROUTER_API_KEY=...
```

Settings surfaces can persist provider keys and Company agent preferences to `.env` and Aider config files. Per-agent environment variables include:

```bash
export AIDER_COMPANY_AGENT_MODELS="product=gpt-5.5,engineering=claude-sonnet-4-5"
export AIDER_COMPANY_MODEL_COO="gpt-5.5"
export AIDER_COMPANY_CACHING_ENGINEERING=true
export AIDER_COMPANY_API_KEY_ENGINEERING=...
export AIDER_COMPANY_LOCAL_ENGINEERING="http://localhost:11434"
```

You only need keys for the providers/models you actually use.

---

## Common workflows

### 1) Classic interactive coding

```bash
aider src/my_file.py tests/test_my_file.py
```

Use upstream Aider commands to add files, ask questions, edit code, run tests, and commit.

### 2) Headless worker task

```bash
aider --headless --msg "Implement the requested endpoint and run targeted tests"
```

Use this for queues, CI jobs, service wrappers, or Discord/desktop backends that need a single non-interactive result.

### 3) Agent loop from Python

```python
from aider.agent.loop import AiderAgentLoop

loop = AiderAgentLoop(coder=coder, enable_prompt_caching=True)
result = await loop.run("Add validation and tests for the payment payload")
```

### 4) COO-routed Company task

Use `NanobotCOO`, Discord messages, browser per-agent tabs, or desktop per-agent tabs to let the COO classify a request, track the session, route to the right department, retry/fallback on failures, and expose status/events to the UI.

### 5) Zero-to-MVP product creation

Use the golden path when you want Aider Plus to turn a product idea into an MVP-shaped implementation while preserving Aider's ability to iterate on that repo afterward:

```bash
aider company create "Build a simple habit tracker web app with login, dashboard, and streaks"
```

Choose a product template for more grounded Product/UX/Engineering/QA handoffs:

```bash
aider company templates
aider company create "Build a webhook API for Stripe events" --template fastapi-backend -- --model gpt-5.5
aider company new "Build a habit tracker" --name habit-tracker --template nextjs-app
aider warehouse list
```

Use `company create` inside the current repo, or use `company new` to create/select a Git-backed product repo inside the central `products/` warehouse before routing the same brief through Company Mode. Built-in templates cover SaaS dashboards, CLI tools, FastAPI backends, Next.js apps, Discord bots, browser extensions, data apps, and internal admin tools. Use `--dry-plan` to preview the generated Company brief without calling a model. See `docs/company/zero_to_mvp.md` for the full lifecycle example.

### 6) Company prototype flow

Use `CompanyOrchestrator`, Discord `/prototype`, direct CLI Company commands, or Company Mode in the GUI/desktop to route a raw product idea through Product ambiguity detection, optional CEO clarification, typed PRD creation, PRD approval, UX/design, Engineering implementation, reviewer revisions, QA, release approval, DevOps, and post-mortem learning.

### 7) Retrieval-aware playbook learning

Let Company workflow runs accumulate audit logs. Post-mortems extract typed lessons into the playbook, `PlaybookManager` deduplicates and bounds them, and `ContextBuilder` injects only relevant lessons for the next task.

### 8) Browser or desktop Company Mode

Start `aider --browser` or `aider --desktop`, choose direct chat, Company workflow, or a specific agent tab; watch the dashboard, approve or reject gates, inspect audit/COO events, edit settings, read the guide, and view project memory without leaving the UI.

---

## Zero-to-MVP golden path

`aider company create` is the primary product-creation entry point. It translates a raw idea into a template-grounded Company brief and runs that brief through Aider's repo-aware implementation loop so v0 remains reviewable and ready for continuous development.

```bash
aider company create "Build a simple habit tracker web app with login, dashboard, and streaks"
```

The template catalog gives Product, UX, Engineering, QA, and DevOps more concrete defaults for common product shapes:

- `saas-dashboard` for authenticated dashboard products.
- `cli-tool` for command-line products.
- `fastapi-backend` for Python API services.
- `nextjs-app` for React/Next.js apps.
- `discord-bot` for Discord command/event bots.
- `browser-extension` for manifest/popup/content-script projects.
- `data-app` for ingestion, transforms, charts, and exports.
- `internal-admin` for operational back-office tools.

Preview the generated brief with `--dry-plan`, list templates with `aider company templates`, and pass normal Aider options after `--`. The full lifecycle example in `docs/company/zero_to_mvp.md` shows the intended proof path from initial idea through clarification, PRD, UX spec, engineering diff, QA failure, engineering revision, passing QA, release approval, DevOps result, and post-mortem playbook entry.

---

## Products warehouse

Aider Plus now adds a thin ChatDev-inspired product studio layer without replacing Aider's repo-native workflow:

> **Warehouse = a registry of Git-backed product repos, not a replacement for repos.**

The default warehouse is a `products/` directory under the current working directory. Each product gets its own normal Git repository, `.aider` state, settings, audit/memory, and future iteration history. The warehouse keeps a lightweight `warehouse.json` registry plus a shared `.aider/coo/` location for cross-product COO memory experiments.

```bash
aider warehouse init ./products
aider company new "Build a habit tracker" --name habit-tracker --template nextjs-app
aider warehouse list
aider warehouse open habit-tracker
aider warehouse status
```

`aider company create` remains the current-repo path. `aider company new` creates or reuses `products/<product-name>/`, initializes Git there, records it in the registry, changes into that repo, and then runs the same template-grounded Company brief so Engineering builds the coherent MVP structure inside the product repo.

---

## Company workflow

The Company system is centered on typed interfaces, validation gates, and persisted state:

- `CompanyTask`: normalized work request with task id, origin/target departments, artifact type, payload, blocking flag, and context.
- `Deliverable`: department output with status, payload/content alias, metadata, task id, artifact type, and department name.
- `PRD` and `ClarificationRequest`: typed Product artifacts for structured requirements, Markdown previews, clarification questions, approval recovery, and revision flow.
- `DesignSpec` / `DesignSpecV2`: typed UX artifacts for screens, routes, components, data contracts, interaction states, accessibility checklists, global state management, error boundaries, and Markdown/JSON handoffs.
- `SchemaGateValidator` and `GateResult`: Engineering-owned UX validation gate that parses strict `DesignSpecV2` JSON, reports field and semantic errors, and produces rejection payloads for retry or blocked handoff.
- `QAFeedback`: structured QA-to-Engineering failure context with failing tests, output excerpts, covered files, recommendations, revision number, and PRD excerpts.
- `CompanyEvent`: lifecycle/audit event emitted by departments, the orchestrator, and UI integrations.
- `Department`: base interface for context requirements, tool allowlists, and task handling.
- `AgentConfig` / `DepartmentConfig`: per-agent model, prompt caching, cache type, and review-iteration settings.
- `CompanyConfig`: top-level orchestration config for department overrides, default cache behavior, cache-stat recording, and optional COO LLM routing.
- `CompanyStateManager`: persistence and recovery wrapper around `ProjectMemory` plus observability and playbook helpers.
- `CompanyLifecycle`: phase transition rules.
- `ApprovalManager`: blocking approval creation, persistence, recovery, and resolution.
- `ContextBuilder`: retrieval-aware context construction for department tasks.
- `PlaybookManager`: bounded, deduplicated, retrieval-queryable lessons learned.
- `CompanyOrchestrator`: submit/route tasks, register departments, emit events, coordinate handoffs, track metrics, apply config, and run post-mortems.
- `NanobotCOO`: session/message-bus router that can front the orchestrator and provide resilient department selection.

### Lifecycle overview

1. A user submits a raw idea or task.
2. Product checks whether the request is clear enough to write a PRD.
3. If the request is ambiguous, Product opens a clarification approval gate so a human can answer targeted questions before PRD drafting resumes.
4. Product generates a typed PRD, converts it to Markdown for review, self-reviews quality, and stores structured PRD metadata for downstream handoffs.
5. PRD approval can block work until a human approves, rejects, or requests changes; requested changes flow back into Product revision handling.
6. UX produces a structured `DesignSpecV2` handoff when the project requires design.
7. The schema gate validates UX JSON structure, required fields, component state coverage, and screen-to-component references; UX gets one automatic retry with gate feedback before the handoff is marked `validation_failed`.
8. Engineering receives relevant PRD/design/schema-gate/playbook context and runs a programmer/reviewer implementation loop.
9. QA runs checks and records pass, fail, or no-test outcomes.
10. Failed QA can send structured feedback back to Engineering for bounded revision cycles.
11. Release approval can block deployment.
12. DevOps records deployment/release completion.
13. Post-mortem handling records outcomes, extracts audit patterns, updates the deduplicated playbook, and advances final lifecycle state.

### Department isolation

Departments declare required context and allowed tools. The orchestrator handles handoffs instead of having departments directly mutate each other's internals. This keeps Product, UX, Engineering, QA, and DevOps separable while preserving shared memory, audit logs, and lifecycle state.

### Human approval gates

Approval gates are persisted in project memory so pending PRD, clarification, release, and escalation decisions survive restarts. Approvals can be handled from CLI, Discord, browser GUI, or desktop UI.

---

## Nanobot COO orchestration

The COO layer adds a small, observable CEO/COO assistant loop on top of Company Mode:

- `COOMessageBus` keeps inbound/outbound queues, queue statistics, bounded event history, formatted dashboard events, and event handlers for browser/desktop/Discord updates.
- `COOSessionManager` persists per-channel/user session history and metadata in project memory, including route history, last COO action, last department, last deliverable, recent errors, and pending human escalations.
- `COOActionDecision` lets the COO answer the CEO directly, ask for clarification, inspect status, update/recall repo-local COO memory, reserve tool use, or delegate into the internal company.
- `COORouteDecision` remains the delegation-level department routing object, normalizing target aliases, confidence/reasoning/strategy fields, and human escalation flags.
- `NanobotCOO` can call an agent loop for JSON action/route decisions when `enable_coo_llm_routing` is enabled, or fall back to deterministic personal actions plus keyword routing.
- Retry wrappers around routing and department execution record final failures, emit warning events, preserve recovery suggestions, and optionally create human-escalation payloads.

---

## Memory, retrieval, learning, and observability

Aider Plus has multiple memory layers:

- **Conversation memory**: recent chat/session content for agent context.
- **Project memory**: repo-scoped `.aider/project_memory.json` data for approvals, audit events, task state, playbooks, observability, COO sessions, and persisted workflow metadata.
- **Dream consolidation**: Discord/session summarization support for longer-running conversations.
- **Repository memory**: repo facts and task context used by the agent loop.
- **MemoryRetriever**: lightweight TF-IDF retrieval over project-memory artifacts.
- **AuditPatternExtractor**: extracts typed patterns from audit logs, QA outcomes, approval loops, and handoff failures.
- **PlaybookManager**: deduplicates, bounds, and retrieves lessons for future department prompts.

Observability data includes task counts by department/status, QA pass/fail/no-test outcomes, reviewer safeguard metrics, prompt-cache run counts, COO bus queue statistics, route histories, escalation metadata, and dashboard-friendly status summaries. The browser and desktop dashboards surface active tasks, deliverables, approvals, audit events, project memory, COO events, and background errors.

---

## Prompt caching and per-agent configuration

Company Mode supports prompt-caching controls at multiple layers:

- `AiderAgentLoop(..., enable_prompt_caching=True, cache_type="auto")` controls agent-loop behavior.
- `AgentLoopConfig` can enable architect/editor orchestration and cache behavior for a specific loop.
- `AgentConfig` / `DepartmentConfig` can set `enable_caching`, `cache_type`, `preferred_model`, and review-iteration limits for COO, Product, UX, Engineering, Reviewer, QA, and DevOps.
- `CompanyConfig` controls defaults and whether cache stats are recorded in project memory.
- Environment variables and GUI settings can override per-agent model, caching, API key, and local endpoint notes.
- Settings helpers write `.env` key/value updates and `.aider.conf.yml`-style config updates so browser and desktop surfaces share configuration behavior.

Recommended defaults keep caching enabled for prompt-heavy agents such as COO, Product, UX, Engineering, and Reviewer while leaving smaller QA/DevOps prompts uncached unless configured otherwise.

---

## MCP integration

Aider Plus treats MCP as an optional external tool/context layer that complements, but does not replace, LiteLLM or LiteLLM Proxy:

- `aider.mcp.MCPClientManager` lazily connects configured MCP servers per project/task scope and supports stdio, Streamable HTTP, and SSE transports when the optional `mcp` extra is installed.
- MCP server settings live in `MCPConfig` / `MCPServerConfig`, separate from model/provider settings, so LiteLLM remains responsible for model routing while MCP remains responsible for tools and context.
- Discovered MCP tools are converted into normal Aider Plus `ToolRegistry` tools named like `mcp__server__tool`, preserving department allowlists, permission failures, and optional human approval gates for high-risk calls.
- `AiderPlusMCPServer` provides a small safe MCP-server façade that can expose status, context/memory, headless-task submission, Company-task submission, pending approvals, and approval/rejection actions.
- Install optional MCP dependencies with `python -m pip install -e '.[mcp]'`; base installs remain MCP-free for CI and minimal local setups.

---

## Discord integration

Discord is intentionally treated as a chat app, not as a second product console. It should receive messages, preserve channel/session identity, and forward those messages into the shared NanobotCOO/company runtime. Product creation, iteration, approvals, audit logs, dashboards, memory consolidation, status inspection, and settings belong to the common runtime and are surfaced fully in desktop, browser, CLI, or API paths.

This keeps Discord thin and hackable: if a feature works through the COO/company runtime, Discord can expose it as chat text without owning separate business logic. The onboarding flow can still prompt for Discord bot configuration, and provider keys can still come from environment/config files.

---

## Browser GUI

The Streamlit browser UI provides:

- Direct chat with the normal Aider coder.
- Company Mode controls and route selection.
- Per-agent chat tabs for COO, Product, UX, Engineering, Reviewer, QA, and DevOps, each with separate chat history and settings-aware routing.
- Company dashboard tabs for status, pending/background runs, deliverables, approvals, audit log, COO/bus activity, and project memory.
- Settings editor for provider API keys, Aider model, per-agent model overrides, per-agent prompt caching, per-agent API keys, local endpoint notes, and arbitrary provider environment variables.
- Guide tab text explaining how the Chat, Company, agent, dashboard, memory, and settings areas fit together.

---

## Native desktop mode

`aider --desktop` launches a native Tkinter app rather than wrapping Streamlit in a webview. It provides:

- Direct Aider chat and Company workflow submission.
- Per-agent tabs for COO/Product/UX/Engineering/Reviewer/QA/DevOps with clear acronym-preserving labels.
- Company dashboard and status panes, approval controls, audit log, project memory, COO events, background error display, and pending-run tracking.
- Settings editor that writes provider keys and per-agent settings to shared environment/config files.
- Built-in guide/help placement for chat tabs, settings fields, and company routing behavior.
- Local lifecycle management for Company background services and clean shutdown of the event loop.

Because it uses Tkinter and the standard library, desktop mode avoids Streamlit, browser windows, pywebview, and WebView2 as runtime requirements.

---

## Model, docs, benchmark, and website assets

This fork keeps upstream Aider's supporting assets and updates them where needed:

- Model settings and aliases under `aider/resources/`.
- Provider/model handling and tests in `aider/models.py` and model test fixtures.
- Website source under `aider/website/`.
- Benchmark and leaderboard assets under `benchmark/` and website history files.
- Requirement, constraint, and packaging metadata for install/test scenarios.

---

## Development

Useful commands:

```bash
python -m pip install -e '.[dev,browser]'
python -m pytest tests/company
python -m pytest tests/company/test_coo_agent_framework.py tests/company/test_settings_helpers.py
python -m pytest tests/company/test_schema_gate.py tests/company/test_ux_department.py
python -m pytest tests/company/test_zero_to_mvp_cli.py tests/company/test_warehouse_cli.py tests/mcp/test_mcp_integration.py
```

When modifying this fork, keep these expectations in mind:

- Preserve upstream Aider behavior unless a change is explicitly part of Aider Plus.
- Keep Company interfaces typed and serializable because project memory, Discord, browser, and desktop all depend on them.
- Add focused tests under `tests/company/` for orchestration, memory, prompt caching, schema gates, routing, settings, and UI helper behavior.
- Prefer explicit handoff metadata over hidden cross-department mutation.
- Keep prompt-caching metadata optional and provider-aware.
- Persist approval/COO/session state through `ProjectMemory` so restarts can recover.

---

## Safety model

Aider Plus is designed to keep autonomous work reviewable and bounded:

- Git diffs and commits remain the core unit of code review.
- Department tool allowlists prevent Product, UX, QA, and DevOps from using tools intended only for other roles.
- Human gates can block PRD approval, clarification answers, release approval, and COO escalations.
- Engineering reviewer loops have bounded iterations and safeguard metrics.
- UX schema validation blocks incomplete design handoffs before implementation.
- QA feedback reroutes failures back to Engineering with structured context instead of silently releasing.
- COO retry/fallback behavior records errors and can escalate to humans instead of looping indefinitely.
- Audit logs and post-mortems make workflow decisions inspectable after the fact.

---

## Roadmap direction

The current repo already has the major Aider Plus foundations: headless agent runtime, Company workflow, Product/UX/Engineering/QA/DevOps departments, COO routing, persistent memory, retrieval-aware learning, prompt-caching controls, Discord integration, browser GUI, native desktop GUI, and status/approval surfaces.

Natural next steps are deeper service deployment adapters, richer long-running task queues, stronger policy controls around tool execution, broader provider-specific cache telemetry, more UI affordances for cross-agent collaboration, and expanded regression coverage as the autonomous workflow grows.

---

## Upstream Aider docs

Because Aider Plus is a fork, upstream Aider documentation remains useful for the base editing engine, CLI behavior, model configuration, repo maps, lint/test hooks, git behavior, and general usage:

- Install docs: https://aider.chat/docs/install.html
- Usage docs: https://aider.chat/docs/usage.html
- LLM/provider docs: https://aider.chat/docs/llms.html
- Config docs: https://aider.chat/docs/config.html
- Git docs: https://aider.chat/docs/git.html
- Website source in this repo: `aider/website/`

### Aider Plus commit additions summary

The table below summarizes the visible non-merge commits that materially added, fixed, or hardened Aider Plus behavior in this branch. Documentation-only README refreshes and copied upstream website/sample-asset updates are intentionally omitted, so this is a product/engineering changelog rather than a list of every time `README.md` changed.

| Commit | What it added or changed |
| --- | --- |
| `c41ef3b` | Bootstrapped this fork on top of upstream Aider and introduced GPT-5.3/GPT-5.4 model variants in the bundled metadata. |
| `f09d706` | Enabled overeager prompting for Claude Sonnet 4.5 model settings. |
| `f939d0a` | Added Claude Sonnet 4.6 and Claude Opus 4.7 model support. |
| `9ce34d1` | Simplified model-name conditional logic in provider/model handling. |
| `b9d8774` | Mapped generic `opus` and `sonnet` aliases to the latest Claude model family entries. |
| `79c45c3` | Disabled deprecated temperature handling for Claude 4 models. |
| `39023f9` | Extended Claude temperature disabling to Opus 4 and gated `thinking_tokens` to models that support it. |
| `93dfacc` | Added Claude Opus 4.7 settings for Bedrock, Vertex, and OpenRouter. |
| `65cb4d3` | Reformatted the `thinking_tokens` model check for clearer maintenance. |
| `cd24a3a` | Updated model-alias test expectations for the refreshed Sonnet and Opus aliases. |
| `308b154` | Added GPT-5.5 model settings across providers and updated model tests. |
| `3ec8ec5` | Refreshed bundled FAQ/history model references and sample token percentages around GPT-5.5. |
| `e56bd79` | Added the first headless-mode and Discord integration scaffolding for non-interactive Aider tasks. |
| `22f3b87` | Split agent-loop context construction into an explicit, testable build step. |
| `79219d2` | Shifted agent context caching toward coder-native message formatting. |
| `847d109` | Preserved Aider prompt-caching behavior when agent context messages are generated. |
| `3a978a7` | Refactored the agent loop to assemble messages through coder-native paths. |
| `46f43ae` | Removed the obsolete `prepare_messages_for_llm` stub. |
| `d121a2d` | Reworked the agent loop so coder-managed formatting is the primary message path. |
| `bd3c1a2` | Used coder message APIs in the agent loop when those APIs are available. |
| `d34e119` | Simplified agent-loop execution by routing message handling through `coder.run`. |
| `8883c2c` | Refined how user messages are handed off inside the agent loop. |
| `af4656f` | Added architect/editor orchestration so planning and implementation can run as separate coder phases. |
| `44a901d` | Added the initial `ToolRegistry` abstraction for centralized agent tool execution. |
| `5979780` | Fixed malformed HTTP scraper `User-Agent` header handling. |
| `4154d4b` | Added dream/consolidation support for Discord session memory. |
| `edda09f` | Added early Company Mode orchestration sketches and typed schemas. |
| `5f80841` | Refactored Discord execution to use `EngineeringDepartment` tasks. |
| `5c14ef7` | Wired the Discord bot flow through `CompanyOrchestrator` scaffolding. |
| `98cc4a0` | Added guided onboarding and first-run setup prompts. |
| `01b0222` | Extended onboarding to prompt for a Discord bot token. |
| `e2c9ef7` | Added desktop app mode for the browser-oriented UI path. |
| `41382aa` | Improved desktop GUI defaults, process lifecycle behavior, cleanup, and related UX. |
| `bf3a772` | Added an OpenRouter API key field to GUI settings. |
| `19f31a7` | Fixed `EngineeringDepartment` handoff into the agent loop. |
| `275507b` | Added `ProductDepartment` and a Discord prototype flow for PRD-first feature requests. |
| `bd6fbe9` | Routed generated PRD context through Company handoffs so Engineering receives structured requirements. |
| `d461fde` | Added a blocking PRD approval handoff before Product work proceeds downstream. |
| `f867288` | Added a project state machine to the orchestrator. |
| `fe4823d` | Persisted Company approval gates in project memory. |
| `6014143` | Added department tool permissions plus QA test execution/reporting. |
| `53cec2a` | Added DevOps and UX stages to the delivery pipeline. |
| `f98a6e5` | Routed approved releases to DevOps instead of stopping at QA approval. |
| `a8a55ed` | Added Company audit logging and a post-mortem playbook path. |
| `5a6191c` | Stabilized core Company interfaces for tasks, deliverables, events, and state. |
| `e1341b1` | Centralized lifecycle approval handling and audit-log viewing. |
| `782b1b9` | Stabilized Company workflow boundaries, handoffs, and inter-department communication. |
| `df324ea` | Hardened dependency checks, model metadata handling, onboarding behavior, schema validation, and lint/test issues. |
| `6c9b22b` | Applied focused cleanup fixes after the broader hardening pass. |
| `63d117c` | Added Company workflow controls to the desktop/Streamlit GUI. |
| `8dd7ff7` | Improved desktop Company workflow background handling, event delivery, pending-run tracking, and safety indicators. |
| `c697cae` | Expanded the desktop Company workflow UI with dashboard, approvals, audit log, memory, routing, and status views. |
| `f8b99c5` | Added an Engineering programmer/reviewer phase loop with Discord/GUI status support and tests. |
| `054d1a8` | Enhanced reviewer intelligence with structured agent feedback extraction and richer review metadata. |
| `3685579` | Improved programmer revision handling so reviewer feedback is incorporated on follow-up implementation passes. |
| `40289e7` | Injected reviewer feedback more completely into programmer revision prompts and metadata. |
| `1c6da07` | Added terminal approval commands and QA failure rerouting back to Engineering. |
| `6078c3d` | Hardened CLI approval handling and added focused tool-permission enforcement tests. |
| `f45c349` | Added TF-IDF memory retrieval, retrieval-aware context injection, memory/observability metrics, and dashboard/status wiring. |
| `a95cc75` | Added retrieval-aware playbook pattern extraction, bounded deduplicated playbook querying, post-mortem learning integration, and tests. |
| `c6ac28c` | Added Company prompt-caching controls, per-department cache configuration, preferred model hooks, and cache observability. |
| `1403bd0` | Refined Company prompt-caching controls and README-described defaults for agent-heavy workflows. |
| `f1219c2` | Added structured Product clarification workflow support for ambiguous product requests. |
| `e451dc2` | Handled clarification approval responses so Product can resume PRD generation from human answers. |
| `c0d1e8a` | Added structured UX design specs with Markdown/JSON handoffs for Engineering. |
| `6f9a4e8` | Integrated structured PRD and design-spec context into Engineering prompts. |
| `b2a2fcc` | Hardened Engineering review context handoffs and added regression tests for reviewer context propagation. |
| `5172ae9` | Strengthened Engineering reviewer handoff metadata, metrics, config controls, and orchestrator integration. |
| `e51ce8f` | Improved Engineering review context handoff formatting and orchestration behavior. |
| `4eeb86c` | Polished Engineering reviewer safeguards, Discord/GUI lifecycle display behavior, audit-pattern extraction, and related tests. |
| `94fc2b1` | Refined Engineering reviewer phase controls and reduced fragile review-loop behavior. |
| `bdc5922` | Added the UX design schema gate with strict `DesignSpecV2` models, semantic checks, blocked handoff payloads, and tests. |
| `3cdd37a` | Propagated UX schema-gate context through orchestrator handoffs so Engineering sees validation status and structured design data. |
| `9640f2f` | Wired UX schema-gate retry flow so invalid design specs get one automatic regeneration attempt with rejection feedback. |
| `cbbe387` | Added tests for UX schema-gate retry handling. |
| `97b1cf7` | Hardened UX structured-output parsing and fallback behavior, including JSON-in-string response coverage. |
| `dd574f2` | Refactored desktop mode into a native, zero-dependency Tkinter launcher. |
| `36b8061` | Added browser UI settings and an agent prompt box. |
| `6289ff6` | Added Nanobot-inspired COO agent orchestration with per-session routing and message-bus concepts. |
| `f320fbc` | Refactored `NanobotCOO` into a clearer routing coordinator. |
| `c627a7a` | Added COO status observability surfaces for route/session/error visibility. |
| `f3dc31e` | Improved Product PRD revision handling when approval reviewers request changes. |
| `bc1faf2` | Added per-agent Company prompt-caching configuration. |
| `62b8428` | Added COO retry and error-routing resilience. |
| `74b9559` | Hardened COO route decision aliases and target normalization. |
| `af1d1b3` | Hardened COO retry escalation handling for final failure paths. |
| `ff59e96` | Added browser and desktop settings editors for provider and per-agent configuration. |
| `0832ccc` | Added per-agent chat tabs and settings-aware routing in the UI. |
| `fbc97b3` | Documented desktop GUI tabs and settings fields in the in-app guide/help text. |
| `7e3d44b` | Polished desktop guide placement and acronym-correct agent labels. |
| `9595966` | Added the optional MCP integration layer, including client config, tool adapters, server façade, optional dependency wiring, docs, and tests. |
| `e2d6d2e` | Repositioned Nanobot COO as a CEO-assistant layer that can answer, ask clarifying questions, inspect status, use memory, or delegate to the internal company. |
| `be2873e` | Added the zero-to-MVP `aider company create` flow, template catalog, dry-plan mode, docs, and CLI tests. |
| `9983101` | Reworked the zero-to-MVP flow into the Company CLI/main entry points with template-grounded briefs and regression coverage. |
| `c600ffe` | Added the products warehouse manager plus `company new`, `warehouse init/list/open/status`, per-product Git repo setup, registry persistence, and tests. |
