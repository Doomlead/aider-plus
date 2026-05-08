<p align="center">
  <a href="https://aider.chat/"><img src="https://aider.chat/assets/logo.svg" alt="Aider Logo" width="280"></a>
</p>

<h1 align="center">Aider Plus</h1>

<p align="center">
<strong>Aider Plus</strong> is an agent-first fork of <a href="https://github.com/Aider-AI/aider">aider-chat</a>. It keeps Aider's git-aware coding engine and adds a headless automation runtime, autonomous tool-calling loop, Product → UX → Engineering → QA → DevOps company workflow, human approval gates, Discord/browser/desktop surfaces, persistent memory, audit logs, observability metrics, and retrieval-backed learning from prior work.
</p>

---

## Table of contents

- [What Aider Plus is](#what-aider-plus-is)
- [What this repo now does](#what-this-repo-now-does)
- [Core capabilities](#core-capabilities)
- [Architecture overview](#architecture-overview)
- [Quickstart](#quickstart)
- [Installation](#installation)
- [Configuration and model providers](#configuration-and-model-providers)
- [Common workflows](#common-workflows)
- [Company workflow](#company-workflow)
- [Memory, retrieval, learning, and observability](#memory-retrieval-learning-and-observability)
- [Discord integration](#discord-integration)
- [GUI and desktop mode](#gui-and-desktop-mode)
- [Model, docs, benchmark, and website assets](#model-docs-benchmark-and-website-assets)
- [Development](#development)
- [Safety model](#safety-model)
- [Roadmap direction](#roadmap-direction)
- [Upstream Aider docs](#upstream-aider-docs)

---

## What Aider Plus is

Aider Plus turns upstream Aider into a foundation for **agentic software delivery**:

1. Gather repository state, repo maps, conversation memory, repo-scoped project memory, user instructions, workflow state, model metadata, retrieval-ranked project artifacts, and relevant historical playbook patterns.
2. Decide whether to answer directly, run one headless task, call agent tools, ask for clarification, or route the request through a structured delivery workflow.
3. Use Aider's coder implementations for real edits, diffs, lint/test hooks, commits, repo-map context, prompt caching, and model-specific prompting.
4. Coordinate larger work through Product, UX, Engineering, QA, DevOps, approval gates, audit logs, bounded revision loops, post-mortems, and persistent task status.
5. Learn from prior sessions by extracting typed audit patterns, deduplicating and bounding a playbook, retrieving only relevant lessons, and tracking cross-run task/QA/token metrics.
6. Expose the same core runtime to terminal commands, scripts, Discord bots, Streamlit/browser UI, native desktop windows, and future service adapters.

In short: this repository is both an interactive AI pair-programmer and an embeddable runtime for autonomous software agents that can plan, implement, review, approve, test, release, observe, and improve from historical outcomes.

---

## What this repo now does

Aider Plus currently includes:

- **Upstream Aider editing engine**: interactive chat, ask, architect, editblock, whole-file, unified-diff, patch editing, repo maps, git operations, auto-commit behavior, lint/test commands, URL scraping, voice/watch/copy-paste helpers, token/cost accounting, model metadata, and the bundled documentation/website source.
- **Headless and bot mode**: `--headless` and `--bot-mode` set integration-friendly defaults for scripted use by disabling pretty output and streaming while auto-approving prompts.
- **CLI approval commands**: `aider approve <gate-id>` and `aider reject <gate-id> [reason]` resolve persisted approval gates from the terminal without requiring Discord or the GUI.
- **Autonomous agent loop**: `AiderAgentLoop` builds structured context, calls LiteLLM-compatible models with tool definitions, dispatches the `aider_coder` tool, tracks bounded iterations, and reports structured results.
- **Architect/editor orchestration**: agent coding tasks can run through an architect planning phase followed by an editor implementation phase.
- **Tool registry and department permissions**: tools are centrally registered, authorization happens before execution, and departments can be restricted by allowlists with structured permission errors.
- **Company workflow engine**: `CompanyOrchestrator` coordinates projects, department registration, lifecycle transitions, handoffs, approval gates, event recording, background task management, audit viewing, post-mortem outcomes, observability, and playbook learning.
- **Product, UX, Engineering, QA, and DevOps departments**: Product drafts PRDs and clarification requests, UX creates design handoffs, Engineering implements, QA runs targeted checks and release reports, and DevOps performs deployment/release completion.
- **Engineering programmer/reviewer loop**: Engineering runs programmer and reviewer phases, extracts structured reviewer feedback, injects that feedback into revision prompts, loops internally up to bounded limits, records reviewer issues, and fails safely if review cannot pass.
- **QA feedback rerouting**: failed QA can route non-blocking feedback back to Engineering for bounded revision cycles instead of treating every QA failure as a terminal stop.
- **Approval gates**: PRD and release approvals can block lifecycle progress, persist across restarts, recover pending approval UIs, and accept approve/reject/request-changes decisions from Discord, GUI, or CLI.
- **Project lifecycle state machine**: projects move through prototyping, design, development, QA, release-ready, deployment, completed, blocked, revision, and post-mortem paths.
- **Discord integration**: Discord sessions can run direct engineering tasks, start `/prototype` product flows, display approval buttons/modals, recover pending approvals, show audit logs, show company status dashboards, consolidate memory, and enforce repo policies.
- **Persistent memory**: project memory stores repo-scoped project state, schema-versioned migrations, pending approvals, audit events, post-mortems, deliverables, release/deployment data, playbooks, observability metrics, and summaries; conversation memory stores Discord/direct-chat history and dream-style consolidations.
- **Retrieval-aware context injection**: long PRDs and playbooks are scored with a lightweight stdlib TF-IDF retriever so departments receive the most relevant context instead of unbounded memory dumps.
- **Learning playbook**: post-mortems and structured audit logs produce typed patterns for coding standards, UX preferences, and deployment gotchas; the playbook deduplicates similar entries, caps category size, and delegates query-time filtering to retrieval.
- **Structured observability**: task count, QA revision cycles, engineering revision cycles, QA pass/no-test/fail rates, turns per phase, per-department token usage, and approximate cost are persisted and surfaced in company status dashboards.
- **Onboarding**: `aider onboard` and `aider init` gather API keys, Discord bot tokens, default repository/workspace settings, and memory defaults.
- **OpenRouter-first helpers**: setup can detect OpenRouter keys, check free-tier status, select matching default OpenRouter models, offer OAuth when no usable key/model exists, and expose OpenRouter key configuration in the GUI.
- **Browser and desktop UI**: the Streamlit GUI supports direct chat plus Company Mode dashboards, approvals, audit logs, project memory views, route selection, bypass controls, background progress, and error/status indicators.
- **Native desktop mode**: `--desktop` launches the Streamlit GUI in a pywebview window with desktop-friendly defaults, port discovery, process cleanup, optional tray support, and debug tooling.
- **Model metadata updates**: this branch carries GPT-5.5 settings plus newer OpenAI, Anthropic Claude, Gemini, DeepSeek, OpenRouter, Bedrock, and Vertex model entries inherited from recent upstream/model-support work.
- **Focused enforcement tests**: the local test suite currently emphasizes company permissions, approval handling, retrieval/playbook learning, pattern extraction, and engineering-review behavior.

---

## Core capabilities

### Coding and repository editing

Aider Plus still behaves like Aider for day-to-day coding:

- Add files to chat, ask questions, request edits, inspect diffs, and commit changes.
- Use model-specific coder modes and prompts.
- Reuse repo maps, lint/test commands, git staging/commit behavior, URL scraping, and token/cost tracking.
- Use regular Aider commands when you do not need the Company workflow.

### Headless automation

Use `--headless` or `--bot-mode` for one-shot, integration-friendly runs:

- Non-interactive defaults for workers, scripts, queues, CI jobs, and bots.
- Auto-approved prompts for trusted automation contexts.
- Reduced terminal decoration and streaming so output is easier to parse.
- Compatible with `--msg` and `--message-file` task inputs.

### Agent tool calling

The agent runtime provides:

- A structured `AiderAgentLoop` around coder execution.
- Tool definitions exposed to LiteLLM-compatible chat models.
- A centrally registered `aider_coder` tool that delegates coding work to Aider.
- Tool result capture, error reporting, and bounded iteration control.
- Optional architect/editor task splitting for plan-then-implement flows.
- Department-aware tool authorization before any registered tool executes.

### Company-style delivery

The Company runtime models a small software organization:

- **Product** turns a raw idea into a PRD or asks clarifying questions.
- **PRD approval** can block downstream work until a human approves, rejects, or requests changes.
- **UX** produces design context when needed.
- **Engineering** implements using programmer/reviewer sub-phases and bounded revision prompts.
- **QA** runs targeted checks, records pass/fail/no-test outcomes, and can route failures back to Engineering for a bounded fix cycle.
- **Release approval** can block deployment.
- **DevOps** records deployment or release completion.
- **Post-mortem learning** extracts patterns from outcomes and adds only novel, bounded, typed lessons to future playbooks.

### Surfaces

- **Terminal**: direct Aider CLI, `--headless`, `--bot-mode`, `--desktop`, onboarding commands, and approval commands.
- **Discord**: bot façade for headless engineering tasks, prototype flows, approval interactions, audit viewing, company status, and memory consolidation.
- **Browser GUI**: Streamlit chat UI with model/key settings, OpenRouter key affordances, Company Mode controls, dashboard tabs, approvals, audit log, and project-memory display.
- **Desktop GUI**: pywebview wrapper around the browser GUI with local-process lifecycle management.
- **Python APIs**: `AiderAgentLoop`, `ToolRegistry`, `CompanyOrchestrator`, department classes, `ProjectMemory`, `ContextBuilder`, `MemoryRetriever`, `PlaybookManager`, `AuditPatternExtractor`, and Discord/session helpers.

---

## Architecture overview

Important runtime areas:

- **Aider core**: `aider/` contains the upstream coding engine, coders, model metadata, CLI, commands, repo map, IO, git integration, browser GUI, and supporting utilities.
- **Agent loop**: `aider/agent/` contains tool definitions, the department-aware `ToolRegistry`, and the Aider-backed agent loop.
- **Company workflow**: `aider/company/` contains the orchestrator, state manager, lifecycle transitions, departments, approval gates, context builder, audit helpers, schemas, and playbook manager.
- **Memory**: `aider/memory/` contains conversation memory, project memory, dream consolidation, repository memory, the TF-IDF retriever, and audit pattern extraction.
- **Integrations**: `aider/integrations/` contains the Discord adapter.
- **GUI and desktop**: `aider/gui.py` and desktop launch paths expose direct chat and Company Mode.
- **Tests**: `tests/company/` contains the focused company workflow, permissions, retrieval, playbook, and review tests.
- **Docs/site/benchmarks**: `aider/website/`, `benchmark/`, and root docs retain upstream assets plus Aider Plus updates.

High-level flow:

```text
User / Script / Discord / GUI
        |
        +--> classic Aider coder path
        |
        +--> Headless AiderAgentLoop
        |       |
        |       +--> ToolRegistry --(authorization)--> aider_coder
        |       |
        |       +--> Aider coder modes, repo map, git, lint/test hooks
        |
        +--> CompanyOrchestrator
                |
                +--> Product -> PRD approval
                +--> UX -> design handoff
                +--> Engineering programmer/reviewer loop
                +--> QA -> pass/fail/no-test metrics or Engineering reroute
                +--> Release approval -> DevOps
                +--> Audit log -> post-mortem -> pattern extraction
                +--> PlaybookManager -> retrieval-ranked future context
                +--> CompanyStateManager -> memory + metrics persistence
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

### Start the native desktop wrapper

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

---

## Installation

For development or local use from this checkout:

```bash
python -m pip install -e .
```

For browser/desktop work, install browser extras:

```bash
python -m pip install -e '.[browser]'
```

For test/development workflows:

```bash
python -m pip install -e '.[dev,browser]'
```

Optional integration dependencies depend on the surface you use:

- Discord bot support requires the Discord Python dependencies expected by `aider/integrations/discord.py`.
- Desktop mode requires `pywebview`; tray support is opportunistic when tray dependencies are available.
- Model providers require the relevant API keys in environment variables, config, onboarding output, or GUI settings.

---

## Configuration and model providers

Aider Plus inherits upstream Aider configuration behavior:

- CLI flags, `.aider.conf.yml`, environment variables, and model/provider settings continue to work.
- Model settings are defined in `aider/resources/model-settings.yml` and provider handling is in `aider/models.py`.
- OpenRouter support is emphasized in onboarding and GUI settings.
- This branch includes model metadata additions for GPT-5.5 and newer provider variants.

Typical provider setup:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export OPENROUTER_API_KEY=...
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

loop = AiderAgentLoop(coder)
result = loop.run("Add validation and tests for the payment payload")
```

### 4) Company prototype flow

Use `CompanyOrchestrator`, Discord `/prototype`, or Company Mode in the GUI to route a raw product idea through PRD creation, approval, UX/design, Engineering implementation, reviewer revisions, QA, release approval, DevOps, and post-mortem learning.

### 5) Retrieval-aware playbook learning

Let Company workflow runs accumulate audit logs. Post-mortems extract typed lessons into the playbook, `PlaybookManager` deduplicates and bounds them, and `ContextBuilder` injects only relevant lessons for the next task.

### 6) Desktop Company Mode

Start `aider --desktop`, enable Company Mode, choose Auto/Prototype/Engineering routing, watch the dashboard, approve or reject gates, inspect audit events, and view project memory without leaving the desktop app.

---

## Company workflow

The Company system is centered on typed interfaces and persisted state:

- `CompanyTask`: normalized work request with task id, department target, description, context, source, payload, and metadata.
- `Deliverable`: department output with status, payload, metadata, task id, and department name.
- `CompanyEvent`: lifecycle/audit event emitted by departments and the orchestrator.
- `Department`: base interface for context requirements, tool allowlists, and task handling.
- `CompanyStateManager`: persistence and recovery wrapper around `ProjectMemory` plus observability and playbook helpers.
- `CompanyLifecycle`: phase transition rules.
- `ApprovalManager`: blocking approval creation, persistence, recovery, and resolution.
- `ContextBuilder`: retrieval-aware context construction for department tasks.
- `PlaybookManager`: bounded, deduplicated, retrieval-queryable lessons learned.
- `CompanyOrchestrator`: submit/route tasks, register departments, emit events, coordinate handoffs, track metrics, and run post-mortems.

### Lifecycle overview

1. A user submits a raw idea or task.
2. Product produces a PRD or asks clarifying questions.
3. PRD approval can block work until a human approves, rejects, or requests changes.
4. UX produces a design handoff when the project requires design.
5. Engineering receives relevant PRD/design/playbook context and runs a programmer/reviewer implementation loop.
6. QA runs checks and records pass, fail, or no-test outcomes.
7. Failed QA can send structured feedback back to Engineering for bounded revision cycles.
8. Release approval can block deployment.
9. DevOps records deployment/release completion.
10. Post-mortem handling records outcomes, extracts audit patterns, updates the deduplicated playbook, and advances final lifecycle state.

### Department isolation

Departments declare required context and allowed tools. The orchestrator handles handoffs instead of having departments directly mutate each other's private state. Tool permissions are checked before tool execution, and violations raise structured `ToolPermissionError` objects suitable for audit/reporting.

---

## Memory, retrieval, learning, and observability

Aider Plus adds memory layers that are separate from git history:

- **Conversation memory** stores message history for long-running bot/session contexts.
- **Dream consolidation** summarizes older conversation context so Discord sessions can stay compact.
- **Project memory** stores repo-scoped project state, schema version, pending approvals, audit logs, deliverables, release/deployment data, post-mortems, playbooks, and observability metrics.
- **Schema migrations** normalize older project memory into the current schema, including structured playbook entries and observability defaults.
- **Audit log events** record department actions, approvals, deliverables, reviewer results, QA pass/fail, deployment status, lifecycle transitions, and post-mortem activity.
- **TF-IDF retrieval** ranks text chunks with stdlib-only cosine similarity so context can remain bounded without external vector services.
- **Retrieval-aware context** slices long PRDs and queries playbook categories against the current task rather than injecting everything.
- **Typed pattern extraction** converts QA failures, CEO/approval rejections, deployment failures, and Engineering reviewer revisions into provenance-carrying playbook entries.
- **Bounded playbooks** deduplicate similar entries, cap each category, preserve legacy string compatibility, and expose query-time retrieval over coding standards, UX preferences, and deployment gotchas.
- **Observability metrics** persist turns per phase, per-department token/cost usage, QA pass/fail/no-test rates, total tasks, QA revision cycles, Engineering revision cycles, and average QA revisions per task.

Audit and memory data is viewable from:

- Discord audit log messages.
- `AuditLogViewer` in Python.
- Desktop helper rendering via `render_desktop_audit_log`.
- GUI Company Mode audit-log and project-memory pages.
- `CompanyOrchestrator.company_status()` dashboard output.
- Raw `.aider/project_memory.json` data.

---

## Discord integration

Discord support lives in `aider/integrations/discord.py` and provides:

- A `DiscordAiderSession` façade around headless Aider/agent execution.
- Per-channel/thread session keys and project memories.
- Direct Engineering tasks through the Aider agent loop.
- Product-led prototype execution through `run_prototype` and `/prototype`-style commands.
- Approval messages with Approve, Reject, and Request Changes controls.
- Pending approval recovery after bot reconnect/restart.
- Audit log rendering for recent company events.
- Company status/dashboard rendering, including observability summaries.
- Conversation memory and dream consolidation for long-running sessions.
- Repository-root policy controls for Discord-triggered work.

Typical adapter flow:

```python
from aider.integrations.discord import DiscordAiderSession

session = DiscordAiderSession(repo_path="/path/to/repo")
result = await session.run_instruction("Implement the requested change")
```

---

## GUI and desktop mode

Aider Plus keeps upstream browser GUI behavior and adds richer Company Mode plus a desktop wrapper:

- `--browser` starts the Streamlit GUI in a browser.
- `--desktop` starts the same Streamlit app on a local port and hosts it in a native pywebview window.
- The desktop wrapper finds an available port, waits for the server, sets desktop-friendly Streamlit flags, cleans up the child process, and optionally starts a tray icon when dependencies are present.
- `--desktop-debug` enables web inspector/devtools support.
- The GUI sidebar can pause/resume Company Mode, select Auto/Prototype/Engineering routing, bypass the next prompt for direct Aider chat, refresh status, and surface pending approvals.
- Main GUI tabs include Chat, Company Dashboard, Approvals, Audit Log, and Project Memory.
- The Company Dashboard shows lifecycle phase progress, pending approvals, recent deliverables, changed files, observability metrics, and raw company status.
- Approval pages provide approve, reject, and request-changes interactions.
- Background workflow execution is isolated from the Streamlit request thread and exposes pending-run and error indicators.
- Model/settings UI includes OpenRouter API key handling.

---

## Model, docs, benchmark, and website assets

The repo retains upstream Aider's documentation, benchmark scaffolding, website source, and model metadata while carrying Aider Plus updates:

- Website source and docs live in `aider/website/`.
- Benchmark tooling and results live in `benchmark/`.
- Model settings live in `aider/resources/model-settings.yml` and `aider/models.py`.
- This branch includes metadata/docs for GPT-5.5 plus newer OpenAI, Anthropic Claude, Gemini, DeepSeek, OpenRouter, Bedrock, and Vertex variants.
- The upstream basic/browser test suite has been trimmed from this branch; current local tests focus on company workflow enforcement and learning behavior.

---

## Development

### Install for development

```bash
python -m pip install -e '.[dev,browser]'
```

### Run tests

```bash
pytest -q tests/company
```

### Useful commands

```bash
python -m aider --help
python -m aider --headless --msg "Summarize this repository"
python -m aider --browser
python -m aider --desktop
python -m aider approve <gate-id>
python -m aider reject <gate-id> "Reason"
```

### Important repo locations

- Core package: `aider/`
- Agent runtime: `aider/agent/`
- Company workflow: `aider/company/`
- Integrations: `aider/integrations/`
- Memory, retrieval, and learning: `aider/memory/`
- Focused local tests: `tests/company/`
- Benchmarks: `benchmark/`
- Website/docs: `aider/website/`
- Utility scripts: `scripts/`

---

## Safety model

Aider Plus follows a practical safety posture for code agents:

- Agent iterations are bounded.
- Engineering reviewer/programmer revisions are bounded.
- QA-to-Engineering revision cycles are bounded.
- Headless mode is explicit and intended for controlled environments.
- Department tool permissions block unauthorized tool use before execution.
- Human approvals can block PRD and release handoffs.
- CLI, Discord, and GUI approval paths all persist through project memory.
- Repository policies can restrict Discord-triggered work to approved roots.
- Prompt size and runtime limits protect bot integrations.
- Retrieval-aware context avoids unbounded memory injection.
- Playbook categories are capped and deduplicated.
- Background GUI tasks expose errors instead of silently failing.
- Persistent audit logs and observability make automated work inspectable over time.
- Git-native outputs keep diffs and commits human-reviewable.

---

## Roadmap direction

All three initial priority tiers are represented in this branch:

- **Short-term**: CLI approval resolution, strict tool-permission enforcement, execution-order safety, bootstrap support, and targeted permission tests.
- **Medium-term**: TF-IDF retrieval, retrieval-aware context injection, schema-v3 project memory migration, structured token/cost/task/QA metrics, orchestrator metrics wiring, and dashboard/status output.
- **Long-term**: typed audit-log pattern extraction, retrieval-deduplicated bounded playbooks, post-mortem learning, context-builder playbook delegation, and pattern/playbook tests.

The next natural capability tier is **cross-session observability**: tooling that reads `task_metrics`, `qa_metrics`, token/cost data, and audit trends across time to surface regressions, reliability drift, and process bottlenecks across repositories or repeated delivery sessions.

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

The following summarizes each non-merge Aider Plus commit visible in this branch's history, in chronological order. Earlier upstream Aider/base commits are intentionally omitted; merge commits are also omitted because they primarily integrate the feature commits listed here.

| Commit | What it added or changed |
| --- | --- |
| `308b154` | Added GPT-5.5 model settings and aliases across providers with model-setting test coverage. |
| `c723364` | Added advanced model settings documentation and refreshed FAQ/sample analytics website content. |
| `3ec8ec5` | Updated FAQ token percentage references and switched the history update script's model reference to GPT-5.5. |
| `e56bd79` | Added initial headless-mode defaults and Discord integration scaffolding. |
| `531da4b` | Documented headless mode and Discord integration support. |
| `b58b443` | Reframed the README around Aider Plus's agent-first direction. |
| `22f3b87` | Split agent-loop context construction into an explicit, testable build step. |
| `79219d2` | Shifted agent context caching toward coder-native message formatting. |
| `847d109` | Preserved Aider prompt caching behavior when generating agent context messages. |
| `3a978a7` | Refactored the agent loop to use coder-native message assembly. |
| `46f43ae` | Removed an obsolete `prepare_messages_for_llm` stub. |
| `d121a2d` | Refactored the agent loop to rely on coder-managed message formatting. |
| `bd3c1a2` | Used coder message APIs in the agent loop when those APIs are available. |
| `d34e119` | Simplified agent-loop message handling by routing through `coder.run`. |
| `8883c2c` | Refined user-message handoff behavior inside the agent loop. |
| `af4656f` | Added architect/editor orchestration so planning and implementation can run as separate coder phases. |
| `44a901d` | Added the initial `ToolRegistry` abstraction for agent tool execution. |
| `5979780` | Fixed malformed HTTP scraper `User-Agent` header handling. |
| `4154d4b` | Added dream/consolidation support for Discord session memory. |
| `edda09f` | Added early company-orchestration sketches and schemas. |
| `5f80841` | Refactored Discord execution to use `EngineeringDepartment` tasks. |
| `5c14ef7` | Wired the Discord bot flow through `CompanyOrchestrator` scaffolding. |
| `98cc4a0` | Added guided onboarding and first-run setup prompts. |
| `01b0222` | Extended onboarding to prompt for a Discord bot token. |
| `3b19a1e` | Rewrote the README with a fuller Aider Plus capabilities overview. |
| `faa0d76` | Added a README summary of Aider Plus commit additions. |
| `e2c9ef7` | Added desktop app mode that wraps the browser GUI in a native desktop window. |
| `41382aa` | Improved desktop GUI defaults, lifecycle handling, process cleanup, and related UX. |
| `bf3a772` | Added an OpenRouter API key field to GUI settings. |
| `19f31a7` | Fixed `EngineeringDepartment` handoff into the agent loop. |
| `275507b` | Added `ProductDepartment` and a Discord prototype flow for PRD-first feature requests. |
| `bd6fbe9` | Routed generated PRD context through company handoffs so Engineering receives structured requirements. |
| `d461fde` | Added a blocking PRD approval handoff before Product work proceeds to UX/Engineering. |
| `f867288` | Added a project state machine to the orchestrator. |
| `fe4823d` | Persisted company approval gates in project memory. |
| `6014143` | Added department tool permissions plus QA test execution/reporting. |
| `53cec2a` | Added DevOps and UX stages to the company delivery pipeline. |
| `f98a6e5` | Routed approved releases to DevOps instead of stopping at QA approval. |
| `a8a55ed` | Added company audit logging and a post-mortem playbook path. |
| `5a6191c` | Stabilized company core interfaces for tasks, deliverables, events, and state. |
| `e1341b1` | Centralized lifecycle approval handling and audit-log viewing. |
| `782b1b9` | Stabilized company workflow boundaries, handoffs, and inter-department communication. |
| `2fb8435` | Updated the README for then-current Aider Plus capabilities. |
| `df324ea` | Hardened dependency checks, model metadata handling, onboarding behavior, schema validation, and lint/test issues. |
| `6c9b22b` | Trimmed the local test tree substantially, leaving focused company workflow tests in this branch. |
| `63d117c` | Added Company workflow controls to the desktop/Streamlit GUI. |
| `8dd7ff7` | Improved desktop Company workflow background handling, event delivery, pending-run tracking, and safety indicators. |
| `c697cae` | Expanded the desktop Company workflow UI with dashboard, approval, audit-log, project-memory, routing, and status improvements. |
| `f8b99c5` | Added an Engineering programmer/reviewer phase loop with Discord/GUI status support and tests. |
| `054d1a8` | Enhanced reviewer intelligence with structured agent feedback extraction and richer review metadata. |
| `3685579` | Improved programmer revision handling so reviewer feedback is incorporated on follow-up implementation passes. |
| `40289e7` | Injected reviewer feedback more completely into programmer revision prompts and metadata. |
| `a2c6080` | Refreshed the README for the then-current Aider Plus system shape. |
| `1c6da07` | Added terminal approval commands and QA failure rerouting back to Engineering. |
| `6078c3d` | Hardened CLI approval handling and added focused tool-permission enforcement tests. |
| `f45c349` | Added TF-IDF memory retrieval, retrieval-aware context injection, schema-v3 memory/observability metrics, and orchestrator dashboard/status wiring. |
| `a95cc75` | Added retrieval-aware playbook pattern extraction, bounded deduplicated playbook querying, post-mortem learning integration, and pattern/playbook tests. |
| `(this commit)` | Fully refreshed this README to describe the completed short-, medium-, and long-term Aider Plus capability tiers and updated the Aider Plus commit summary. |
