<p align="center">
  <a href="https://aider.chat/"><img src="https://aider.chat/assets/logo.svg" alt="Aider Logo" width="280"></a>
</p>

<h1 align="center">Aider Plus</h1>

<p align="center">
<strong>Aider Plus</strong> is an agent-first fork of <a href="https://github.com/Aider-AI/aider">aider-chat</a>. It keeps Aider's git-aware coding engine and layers on headless automation, an autonomous tool-calling agent loop, a Product → UX → Engineering → QA → DevOps company workflow, Discord and desktop surfaces, onboarding, persistent project memory, audit trails, approval gates, and newer model/provider metadata.
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
- [Discord integration](#discord-integration)
- [Memory and auditability](#memory-and-auditability)
- [GUI and desktop mode](#gui-and-desktop-mode)
- [Model, docs, benchmark, and website assets](#model-docs-benchmark-and-website-assets)
- [Development](#development)
- [Safety model](#safety-model)
- [Roadmap direction](#roadmap-direction)
- [Upstream Aider docs](#upstream-aider-docs)

---

## What Aider Plus is

Aider Plus turns upstream Aider into a foundation for **agentic software delivery**:

1. Gather repository state, repo maps, conversation memory, project memory, user instructions, model metadata, and workflow state.
2. Decide whether to answer directly, run a headless task, call agent tools, ask for clarification, or route the request through a structured delivery workflow.
3. Use Aider's coder implementations for real edits, diffs, lint/test hooks, commits, repo-map context, and model-specific prompting.
4. Coordinate larger work through Product, UX, Engineering, QA, DevOps, approval gates, audit logs, revision loops, and post-mortems.
5. Expose the same core runtime to terminal, scripts, Discord bots, Streamlit/browser UI, native desktop windows, and future service adapters.

In short: this repository is both an interactive AI pair-programmer and an embeddable runtime for autonomous software agents that can plan, implement, review, approve, and track feature delivery.

---

## What this repo now does

Aider Plus currently includes:

- **Upstream Aider editing engine**: interactive chat, ask, architect, editblock, whole-file, unified-diff, patch editing, repo maps, git operations, auto-commit behavior, lint/test commands, URL scraping, voice/watch/copy-paste helpers, token/cost accounting, model metadata, and the bundled documentation/website source.
- **Headless and bot mode**: `--headless` and `--bot-mode` set integration-friendly defaults for scripted use by disabling pretty output and streaming while auto-approving prompts.
- **Autonomous agent loop**: `AiderAgentLoop` builds structured context, calls LiteLLM-compatible models with tool definitions, dispatches the `aider_coder` tool, tracks bounded iterations, and reports structured results.
- **Architect/editor orchestration**: agent coding tasks can run through an architect planning phase followed by an editor implementation phase.
- **Tool registry and department permissions**: tools are centrally registered and can be restricted by the active company department's allowlist.
- **Company workflow engine**: `CompanyOrchestrator` coordinates projects, department registration, lifecycle transitions, handoffs, approval gates, event recording, background task management, audit viewing, and post-mortem outcomes.
- **Product, UX, Engineering, QA, and DevOps departments**: Product drafts PRDs and clarification requests, UX creates design handoffs, Engineering implements, QA runs targeted checks and release reports, and DevOps performs deployment/release completion.
- **Engineering programmer/reviewer loop**: Engineering now runs programmer and reviewer phases, injects structured reviewer feedback into revision prompts, loops internally up to bounded limits, records reviewer issues, and fails safely if review cannot pass.
- **Approval gates**: PRD and release approvals can block lifecycle progress, persist across restarts, recover pending approval UIs, and accept approve/reject/request-changes decisions.
- **Project lifecycle state machine**: projects move through prototyping, design, development, QA, release-ready, deployment, completed, blocked, revision, and post-mortem paths.
- **Discord integration**: Discord sessions can run direct engineering tasks, start `/prototype` product flows, display approval buttons/modals, recover pending approvals, show audit logs, show company status dashboards, consolidate memory, and enforce repo policies.
- **Persistent memory**: project memory stores repo-scoped project state, pending approvals, audit events, post-mortems, deliverables, and summaries; conversation memory stores Discord/direct-chat history and dream-style consolidations.
- **Onboarding**: `aider onboard` and `aider init` gather API keys, Discord bot tokens, default repository/workspace settings, and memory defaults.
- **OpenRouter-first helpers**: setup can detect OpenRouter keys, check free-tier status, select matching default OpenRouter models, offer OAuth when no usable key/model exists, and expose OpenRouter key configuration in the GUI.
- **Browser and desktop UI**: the Streamlit GUI supports direct chat plus Company Mode dashboards, approvals, audit logs, project memory views, route selection, bypass controls, background progress, and error/status indicators.
- **Native desktop mode**: `--desktop` launches the Streamlit GUI in a pywebview window with desktop-friendly defaults, port discovery, process cleanup, optional tray icon support, debug/devtools mode, and audit-log rendering helpers.
- **Model updates**: model settings and docs include newer OpenAI, Anthropic, Gemini, DeepSeek, and OpenRouter metadata, including GPT-5.5 aliases and provider-specific Claude/GPT/Gemini variants inherited from this branch.
- **Focused test coverage**: the retained local tests focus on the newer Engineering reviewer/programmer revision loop.

---

## Core capabilities

### Aider-compatible coding assistant

- Runs from a terminal, a browser-based Streamlit GUI, or a native desktop shell.
- Uses Aider's coder implementations for edits instead of replacing the editing engine.
- Preserves repo-map context, prompt caching, model settings, token accounting, git workflows, auto-commit behavior, lint/test commands, file watching, URL ingestion, and existing config conventions.
- Keeps upstream Aider documentation and website assets in the repository for the base CLI/editing behavior.

### Autonomous agent loop

- Builds an `AgentContext` from conversation memory, recent coder/tool results, repository metadata, project instructions, project memory, and the current department context.
- Calls LiteLLM-compatible chat models with tool schemas and bounded iteration counts.
- Emits lifecycle callbacks such as `context_built`, `thinking`, `planning_with_architect`, `executing_edits`, `permission_violation`, and `response_complete`.
- Returns structured summaries, iteration counts, coder results, errors, diffs, changed files, and commits when available.
- Supports coder-native message formatting so upstream prompt caching and model-specific message preparation keep working.

### Company-style delivery

- Product turns raw feature ideas into PRDs and clarification requests.
- UX receives PRD context and produces design specifications or handoffs.
- Engineering runs the Aider agent loop, uses architect/editor staging, then runs an internal reviewer phase.
- Reviewer feedback is structured, summarized, persisted in deliverable metadata, and injected into programmer revisions until review passes or the bounded loop fails.
- QA executes targeted checks and produces release reports.
- Release approval can route approved work to DevOps for deployment completion.
- Failures, rejected approvals, or requested changes can route work into revision, blocked, or post-mortem states.

### Multi-surface operation

- **Terminal**: direct Aider CLI, `--headless`, `--bot-mode`, `--desktop`, and normal Aider subcommands.
- **Discord**: bot façade for headless engineering tasks, prototype flows, approval interactions, audit viewing, and company status.
- **Browser GUI**: Streamlit chat UI with model/key settings, OpenRouter key affordances, Company Mode controls, dashboard tabs, approvals, audit log, and project-memory display.
- **Desktop GUI**: native wrapper around the browser GUI with lifecycle cleanup, desktop defaults, optional tray icon, and debug mode.

---

## Architecture overview

Primary modules:

- **CLI and runtime entrypoint**: `aider/main.py`, `aider/args.py`
- **Aider editing engine**: `aider/coders/`, `aider/commands.py`, `aider/repo.py`, `aider/repomap.py`, `aider/models.py`
- **Agent runtime**: `aider/agent/loop.py`, `aider/agent/tools.py`
- **Company workflow**: `aider/company/`
- **Discord adapter**: `aider/integrations/discord.py`
- **Memory**: `aider/memory/`
- **Onboarding and provider setup**: `aider/onboarding.py`, `aider/onboard.py`, OpenRouter helpers in the main runtime
- **GUI/desktop**: `aider/gui.py`, `aider/desktop.py`
- **Docs/website/benchmarks**: `aider/website/`, `benchmark/`
- **Focused local tests**: `tests/company/`

High-level flow:

```text
Terminal / script / Discord / browser GUI / desktop GUI
        |
        v
CLI configuration + model/provider setup + repo context
        |
        +--> Direct Aider coder flow
        |
        +--> AiderAgentLoop
        |       +--> AgentContext + ToolRegistry + aider_coder tool
        |       +--> optional Architect -> Editor editing phases
        |
        +--> CompanyOrchestrator
                +--> Product -> UX -> Engineering -> QA -> DevOps
                +--> approvals, project lifecycle, audit log, memory, post-mortem
                +--> desktop/Discord status and approval surfaces
```

---

## Quickstart

### Run normal Aider-compatible chat

```bash
aider
```

### Run one headless task

```bash
aider --headless --model gpt-5.5 --msg "Refactor the parser and add tests"
```

`--headless` also works as `--bot-mode` and is intended for scripts, queues, CI jobs, Discord workers, and service wrappers.

### Start guided setup

```bash
aider onboard
# or
aider init
```

### Launch the browser GUI

```bash
aider --browser
```

### Launch the desktop GUI

```bash
aider --desktop
```

Use `--desktop-debug` when you need web inspector/devtools for the desktop shell.

---

## Installation

Aider Plus follows the upstream package layout and still publishes the `aider` console script from `aider.main:main`.

### From this repository

```bash
git clone <this-repo-url>
cd aider-plus
python -m pip install -e '.[dev,browser]'
```

### Runtime expectations

- Python `>=3.10,<3.15`.
- A git repository for most editing and GUI workflows.
- At least one model provider key, such as OpenAI, Anthropic, OpenRouter, or another LiteLLM-supported provider.
- Browser/desktop mode needs the browser optional dependencies. Desktop mode additionally uses pywebview at runtime.
- Discord integration requires a Discord bot token and a service wrapper that instantiates the Discord session helper.

---

## Configuration and model providers

Aider Plus keeps Aider's provider/config style and adds setup conveniences:

- Use normal Aider configuration files, environment variables, and CLI flags for models and API keys.
- Use `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, or other LiteLLM-compatible provider configuration.
- `OPENROUTER_API_KEY` can be checked for free-tier status so setup can select a matching default model.
- When no usable key/model is detected, setup can offer OpenRouter OAuth.
- GUI settings expose model/key configuration, including an OpenRouter API key field.
- Model metadata has been refreshed for newer GPT, Claude, Gemini, DeepSeek, and OpenRouter entries, including GPT-5.5 support.

---

## Common workflows

### 1) Direct pair programming

Run `aider` and use normal Aider chat/edit commands for interactive coding.

### 2) Scripted automation

Use `--msg` or `--message-file` with `--headless` to run a single task from a script, queue worker, CI job, Discord worker, or service adapter.

### 3) Agent-loop execution

Use `AiderAgentLoop` when you want model-directed tool calling, structured context, architect/editor staging, callbacks, department permissions, and structured results.

### 4) Company prototype flow

Use `CompanyOrchestrator`, Discord `/prototype`, or Company Mode in the GUI to route a raw product idea through PRD creation, approval, UX/design, engineering implementation, reviewer revisions, QA, release approval, and DevOps.

### 5) Desktop Company Mode

Start `aider --desktop`, enable Company Mode, choose Auto/Prototype/Engineering routing, watch the dashboard, approve or reject gates, inspect audit events, and view project memory without leaving the desktop app.

### 6) Audit and recovery

Inspect persisted project memory to recover approvals, review audit logs, see deliverables, and understand why a project moved to revision, blocked, deployed, completed, or post-mortem.

---

## Company workflow

The company system is implemented under `aider/company/`.

### Key abstractions

- `CompanyTask`: normalized work request with task id, department target, description, context, source, and metadata.
- `Deliverable`: department output with artifact type, payload, status, metadata, optional reviewer feedback, and review status.
- `CompanyEvent`: lifecycle/audit event emitted by departments and the orchestrator.
- `EventMessage`: structured handoff and status messages used by the GUI/orchestrator surfaces.
- `Project`: active project state, phase, PRD, design spec, engineering result, QA result, deployment result, and post-mortem data.
- `CompanyStateManager`: persistence and recovery wrapper around `ProjectMemory`.
- `LifecycleEngine`: centralized project phase transitions.
- `ApprovalManager`: blocking approval creation, persistence, recovery, and resolution.
- `AuditLog` / `AuditLogViewer`: append and render structured company events.

### Delivery stages

1. **Product** creates PRDs and clarification outputs.
2. **PRD approval** can block work until a human approves, rejects, or requests changes.
3. **UX** creates design context when the task needs a design handoff.
4. **Engineering programmer phase** uses the Aider agent loop to implement.
5. **Engineering reviewer phase** checks the implementation, produces structured feedback, and can trigger programmer revisions.
6. **QA** runs targeted commands/checks and creates release reports.
7. **Release approval** can block deployment.
8. **DevOps** records deployment/release completion for approved releases.
9. **Post-mortem** captures outcomes for completed or failed paths.

### Department isolation

Departments declare required context and allowed tools. The orchestrator handles handoffs instead of having departments directly mutate each other's private state. Tool permissions can block a department from invoking tools outside its allowlist.

---

## Discord integration

Discord support lives in `aider/integrations/discord.py` and provides:

- A `DiscordAiderSession` façade around headless Aider/agent execution.
- Per-channel/thread session keys and project memories.
- Direct engineering tasks through the Aider agent loop.
- Product-led prototype execution through `run_prototype` and `/prototype`-style commands.
- Approval messages with Approve, Reject, and Request Changes controls.
- Pending approval recovery after bot reconnect/restart.
- Audit log rendering for recent company events.
- Company status/dashboard rendering.
- Conversation memory and dream consolidation for long-running sessions.
- Repository-root policy controls for Discord-triggered work.

Typical adapter flow:

```python
from aider.integrations.discord import DiscordAiderSession

session = DiscordAiderSession(repo_path="/path/to/repo")
result = await session.run_instruction("Implement the requested change")
```

---

## Memory and auditability

Aider Plus adds memory layers that are separate from git history:

- **Conversation memory** stores message history for long-running bot/session contexts.
- **Dream consolidation** summarizes older conversation context so Discord sessions can stay compact.
- **Project memory** stores repo-scoped project state, pending approvals, audit logs, deliverables, release data, deployment data, and post-mortems.
- **Audit log events** record department actions, approvals, deliverables, reviewer results, QA pass/fail, deployment status, lifecycle transitions, and post-mortem activity.

Audit data is viewable from:

- Discord audit log messages.
- `AuditLogViewer` in Python.
- Desktop helper rendering via `render_desktop_audit_log`.
- GUI Company Mode audit-log pages.
- Raw project memory data.

---

## GUI and desktop mode

Aider Plus keeps upstream browser GUI behavior and adds richer Company Mode plus a desktop wrapper:

- `--browser` starts the Streamlit GUI in a browser.
- `--desktop` starts the same Streamlit app on a local port and hosts it in a native pywebview window.
- The desktop wrapper finds an available port, waits for the server, sets desktop-friendly Streamlit flags, cleans up the child process, and optionally starts a tray icon when dependencies are present.
- `--desktop-debug` enables web inspector/devtools support.
- The GUI sidebar can pause/resume Company Mode, select Auto/Prototype/Engineering routing, bypass the next prompt for direct Aider chat, refresh status, and surface pending approvals.
- Main GUI tabs include Chat, Company Dashboard, Approvals, Audit Log, and Project Memory.
- The Company Dashboard shows lifecycle phase progress, pending approvals, recent deliverables, changed files, and raw company status.
- Approval pages provide approve, reject, and request-changes interactions.
- Background workflow execution is isolated from the Streamlit request thread and exposes pending-run and error indicators.
- Model/settings UI includes OpenRouter API key handling.

---

## Model, docs, benchmark, and website assets

The repo retains upstream Aider's documentation, benchmark scaffolding, website source, and model metadata while carrying Aider Plus updates:

- Website source and docs live in `aider/website/`.
- Benchmark tooling and results live in `benchmark/`.
- Model settings live in `aider/resources/model-settings.yml` and `aider/models.py`.
- This branch includes metadata/docs for GPT-5.5 plus newer OpenAI, Anthropic Claude, Gemini, DeepSeek, and OpenRouter variants.
- The upstream basic/browser test suite has been trimmed from this branch; current local tests focus on company engineering review behavior.

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
```

### Important repo locations

- Core package: `aider/`
- Agent runtime: `aider/agent/`
- Company workflow: `aider/company/`
- Integrations: `aider/integrations/`
- Memory: `aider/memory/`
- Focused local tests: `tests/company/`
- Benchmarks: `benchmark/`
- Website/docs: `aider/website/`
- Utility scripts: `scripts/`

---

## Safety model

Aider Plus follows a practical safety posture for code agents:

- Agent iterations are bounded.
- Engineering reviewer/programmer revisions are bounded.
- Headless mode is explicit and intended for controlled environments.
- Department tool permissions can block unauthorized tool use.
- Human approvals can block PRD and release handoffs.
- Repository policies can restrict Discord-triggered work to approved roots.
- Prompt size and runtime limits protect bot integrations.
- Background GUI tasks expose errors instead of silently failing.
- Persistent audit logs make automated work inspectable.
- Git-native outputs keep diffs and commits human-reviewable.

---

## Roadmap direction

Near-term evolution is oriented around:

- stronger agent planning and recovery,
- richer structured outputs for service/bot integrations,
- more robust company lifecycle policies,
- additional channel adapters beyond Discord,
- deeper memory and retrieval behavior,
- more complete desktop/company dashboards,
- broader test coverage after the local test-suite trim,
- better benchmark coverage for end-to-end delivery workflows.

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

The following summarizes each non-merge Aider Plus commit visible in this branch's history, in chronological order. Merge commits are omitted because they primarily integrate the feature commits listed here.

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
| `6c9b22b` | Trimmed the local test tree substantially, leaving the focused company-review tests now present in this branch. |
| `63d117c` | Added Company workflow controls to the desktop/Streamlit GUI. |
| `8dd7ff7` | Improved desktop Company workflow background handling, event delivery, pending-run tracking, and safety indicators. |
| `c697cae` | Expanded the desktop Company workflow UI with dashboard, approval, audit-log, project-memory, routing, and status improvements. |
| `f8b99c5` | Added an Engineering programmer/reviewer phase loop with Discord/GUI status support and tests. |
| `054d1a8` | Enhanced reviewer intelligence with structured agent feedback extraction and richer review metadata. |
| `3685579` | Improved programmer revision handling so reviewer feedback is incorporated on follow-up implementation passes. |
| `40289e7` | Injected reviewer feedback more completely into programmer revision prompts and metadata. |
