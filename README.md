<p align="center">
  <a href="https://aider.chat/"><img src="https://aider.chat/assets/logo.svg" alt="Aider Logo" width="280"></a>
</p>

<h1 align="center">Aider Plus</h1>

<p align="center">
<strong>Aider Plus</strong> is an agent-first fork of <a href="https://github.com/Aider-AI/aider">aider-chat</a>. It preserves Aider's git-aware coding engine and adds an autonomous agent loop, a company-style product delivery workflow, Discord automation, onboarding, persistent project memory, OpenRouter-first setup helpers, and desktop/browser UI improvements.
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
- [Benchmarking, docs, and website](#benchmarking-docs-and-website)
- [Development](#development)
- [Safety model](#safety-model)
- [Roadmap direction](#roadmap-direction)
- [Upstream Aider docs](#upstream-aider-docs)

---

## What Aider Plus is

Aider Plus turns upstream Aider into a foundation for **agentic software delivery**:

1. Read repository state, project memory, conversation history, and user instructions.
2. Decide whether to answer directly, plan, ask for clarification, or execute code edits.
3. Use Aider's established coder pipeline for git-aware implementation.
4. Route larger requests through product, UX, engineering, QA, DevOps, and approval stages.
5. Emit structured events and deliverables for Discord, desktop panels, bots, logs, and future service wrappers.

In short: this repository is both an interactive coding assistant and an embeddable runtime for software agents that can coordinate a full feature delivery loop.

---

## What this repo now does

Aider Plus currently includes:

- **Upstream Aider code editing**: chat, ask, architect, editblock, whole-file, unified-diff, patch, lint/test hooks, repo maps, git commits, URL scraping, voice/watch/copy-paste helpers, model metadata, and the full documentation website source.
- **Headless/bot mode**: `--headless` / `--bot-mode` switches Aider into non-interactive defaults for integrations by disabling pretty output and streaming while auto-approving prompts.
- **Agent loop runtime**: `AiderAgentLoop` builds structured context, calls an LLM with tool definitions, and dispatches an `aider_coder` tool through bounded iterations.
- **Architect/editor orchestration**: coding tasks can run as a two-phase process where an architect coder creates an implementation plan and an editor coder applies it.
- **Tool registry and permissions**: agent tools are registered centrally and can be restricted by department-level tool allowlists.
- **Company workflow engine**: `CompanyOrchestrator` coordinates projects, lifecycle transitions, handoffs, approval gates, audit logging, and post-mortem outcomes.
- **Departments**: Product drafts PRDs and handles clarification, UX creates design handoffs, Engineering runs the Aider agent loop, QA runs targeted checks and produces release reports, and DevOps handles deployment/release completion.
- **Approval gates**: PRD approvals and release approvals can block work, persist across restarts, recover pending approval UIs, and accept approve/reject/request-changes outcomes.
- **Project state machine**: projects move through prototyping, design, development, QA, release-ready, deployment, completed, blocked, revision, and post-mortem paths.
- **Discord integration**: Discord sessions can run direct engineering tasks or `/prototype` product flows, display approval buttons/modals, show audit logs, and show company dashboards.
- **Persistent memory**: project memory stores state, pending approvals, audit events, post-mortems, and consolidated conversation summaries.
- **Onboarding**: `aider onboard` / `aider init` gathers API keys, Discord bot token, default repository, workspace, and project-memory defaults.
- **OpenRouter support**: setup helpers can detect an OpenRouter key, check free-tier status, select default OpenRouter models, or offer OAuth when no key/model is configured.
- **Desktop app mode**: `--desktop` launches the Streamlit GUI inside a native pywebview window with improved size defaults, lifecycle cleanup, optional tray support, and audit-log rendering helpers.
- **GUI settings updates**: the browser/desktop GUI includes model/key configuration affordances including OpenRouter API key handling.
- **GPT-5.5 model metadata**: model settings and tests include GPT-5.5 aliases across supported providers, and docs/history references were updated accordingly.

---

## Core capabilities

### Aider-compatible coding assistant

- Runs interactively from a terminal, browser GUI, or desktop shell.
- Uses Aider's coder implementations for real edits rather than replacing the editing engine.
- Preserves repo-map context, prompt caching, model settings, git workflows, auto-commit behavior, lint/test commands, and existing config conventions.

### Autonomous agent loop

- Builds an `AgentContext` from conversation memory, recent coder/tool results, repository metadata, project instructions, and project memory.
- Calls LiteLLM-compatible models with tool schemas and bounded iteration counts.
- Emits lifecycle callbacks such as `context_built`, `thinking`, `planning_with_architect`, `executing_edits`, `permission_violation`, and `response_complete`.
- Returns structured summaries, iteration counts, coder results, errors, diffs, file lists, and commits when available.

### Company-style delivery pipeline

- Product turns a raw request into a PRD and decides whether UX design is required.
- UX prepares design context when a product request needs design work.
- Engineering receives structured PRD/design context and runs the Aider agent loop.
- QA evaluates changed test files or recommends manual verification when no targeted tests exist.
- Release approval can hand off to DevOps.
- DevOps performs release/deployment reporting and can move work into completion or post-mortem paths.

### Human approvals and governance

- Blocking tasks create approval events with gate names, previews, approver roles, project names, and handoff targets.
- Approvals are persisted in project memory so a Discord or service process can recover pending gates after reconnect/restart.
- Duplicate approvals are ignored after resolution.
- Users can approve, reject, or request changes with feedback.

### Integrations and UI surfaces

- Discord integration exposes reusable bot façade classes plus a `discord.py` client builder.
- Discord commands include audit log viewing, company status/dashboard viewing, and prototype flow kickoff.
- Browser GUI remains available through upstream `--gui` / `--browser` behavior.
- Desktop GUI wraps the Streamlit app in a native window via `--desktop`.

---

## Architecture overview

Important package areas:

- **CLI and argument parsing**: `aider/main.py`, `aider/args.py`, `aider/__main__.py`
- **Aider coder engine**: `aider/coders/`
- **Agent loop**: `aider/agent/loop.py`, `aider/agent/tools.py`
- **Company orchestration**: `aider/company/orchestrator.py`, `aider/company/lifecycle.py`, `aider/company/approval.py`, `aider/company/state.py`
- **Departments**: `aider/company/departments/product.py`, `engineering.py`, `ux.py`, `qa.py`, `devops.py`
- **Company contracts**: `aider/company/schemas.py`, `aider/company/interfaces.py`, `aider/company/project.py`, `aider/company/context.py`
- **Discord adapter**: `aider/integrations/discord.py`
- **Memory**: `aider/memory/conversation.py`, `aider/memory/project.py`, `aider/memory/project_memory.py`, `aider/memory/dream.py`, `aider/memory/repository.py`
- **Onboarding and model setup**: `aider/onboarding.py`, `aider/openrouter.py`, `aider/models.py`, `aider/resources/model-settings.yml`, `aider/resources/model-metadata.json`
- **GUI/desktop**: `aider/gui.py`, `aider/desktop.py`
- **Docs/website**: `aider/website/`
- **Benchmarks**: `benchmark/`
- **Tests**: `tests/`

High-level runtime flow:

```text
User / Discord / GUI / automation
        |
        v
CLI, bot façade, or desktop/browser wrapper
        |
        +--> direct Aider coder flow
        |
        +--> AiderAgentLoop
        |       +--> architect plan
        |       +--> editor implementation
        |
        +--> CompanyOrchestrator
                +--> Product -> approval -> UX? -> Engineering -> QA -> approval -> DevOps
                +--> project memory, audit log, lifecycle state, post-mortem
```

---

## Quickstart

### Interactive terminal usage

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
export OPENAI_API_KEY=your_key_here
cd /path/to/your/repo
aider
```

### Guided setup

```bash
aider onboard
# or
aider init
```

The onboarding flow can collect provider keys, an optional Discord bot token, workspace/repository defaults, and initial project memory.

### Headless automation usage

```bash
export OPENAI_API_KEY=your_key_here
cd /path/to/your/repo
aider --headless --model gpt-5.5 --msg "Refactor the parser and add tests"
```

`--headless` also works as `--bot-mode` and sets integration-friendly defaults.

### Browser GUI

```bash
aider --gui
# or
aider --browser
```

### Desktop GUI

```bash
aider --desktop
```

Use `--desktop-debug` when you need web inspector/devtools for the desktop shell.

---

## Installation

### Standard local install

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### Development install

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
pip install -r requirements/requirements-dev.txt
```

### Optional dependencies

- Browser/Playwright-related requirements live under `requirements/requirements-browser*.txt`.
- Discord integrations require `discord.py`.
- Desktop app mode requires `pywebview`; tray support is optional and depends on `pystray` plus `Pillow`.
- Provider-specific SDKs and API keys follow the same general patterns as upstream Aider/LiteLLM.

---

## Configuration and model providers

Aider Plus keeps Aider's model/provider configuration style and adds convenience behavior around OpenRouter:

- API keys can come from environment variables or onboarding config.
- CLI flags and config files still drive model selection.
- LiteLLM-style provider/model names are supported.
- If no explicit model is supplied, setup helpers can select a default based on available provider keys.
- If `OPENROUTER_API_KEY` is present, Aider Plus can check whether the key is free-tier and choose a matching OpenRouter default.
- If no usable key is detected, Aider Plus can offer an OpenRouter OAuth flow.
- GPT-5.5 settings and aliases are included in model resources/tests.

Useful files:

- `aider/resources/model-settings.yml`
- `aider/resources/model-metadata.json`
- `aider/models.py`
- `aider/openrouter.py`
- `aider/onboarding.py`
- `aider/website/docs/llms/`
- `aider/website/docs/config/`

---

## Common workflows

### 1) Interactive coding assistant

Use Aider the normal way: chat in a repo, request edits, review diffs, run tests, and commit through your usual git flow.

### 2) One-shot automation

Use `--msg` or `--message-file` with `--headless` to run a single task from a script, queue worker, CI job, or service wrapper.

### 3) Agentic implementation

Use `AiderAgentLoop` when you want model-directed tool calling, structured context, architect/editor staging, callbacks, and structured results.

### 4) Company prototype flow

Use the company orchestrator or Discord `/prototype` command to route a raw product idea through PRD creation, approval, design when needed, engineering implementation, QA, release approval, and DevOps.

### 5) Audit and recovery

Use project memory and the audit log viewer to inspect what departments did, recover pending approval gates, and preserve context across sessions.

---

## Company workflow

The company layer models software delivery as explicit tasks and deliverables.

### Main objects

- `CompanyTask`: work request with origin, target, artifact type, payload, blocking flag, context, and metadata.
- `Deliverable`: department output with artifact type, payload/content, status, and handoff metadata.
- `EventMessage`: structured lifecycle/approval/audit event for integrations.
- `Project`: active project state, phase, PRD, design spec, engineering result, QA result, deployment result, and post-mortem data.
- `CompanyStateManager`: persistence and recovery wrapper around `ProjectMemory`.
- `LifecycleEngine`: phase transition policy.
- `ApprovalManager`: blocking approval creation, persistence, recovery, and resolution.

### Phase flow

Typical successful flow:

```text
prototyping
  -> PRD approval
  -> design (optional UX)
  -> development
  -> QA
  -> release approval
  -> deploying
  -> completed
```

Failure or feedback paths can route work into revision, blocked, or post-mortem states.

### Department responsibilities

- **Product**: creates PRDs, preserves original requests, determines UX need, handles engineering clarification memos, and sets PRD approval metadata.
- **UX**: converts PRD context into design guidance before Engineering when required.
- **Engineering**: validates handoff context, composes implementation prompts, runs the Aider agent loop, reports files/commits/diffs, and requests Product clarification when blocked.
- **QA**: checks changed test files with targeted pytest commands when possible, produces release reports, and recommends further verification.
- **DevOps**: processes approved release handoffs and records deployment/release status.

---

## Discord integration

`aider/integrations/discord.py` provides optional Discord support without making `discord.py` a core dependency.

Capabilities:

- Session keys scoped by guild, channel, user, and repository path.
- Repository whitelist policy.
- Prompt size and runtime limits.
- Allow/deny user controls.
- Direct engineering execution through `run_instruction`.
- Product-led prototype execution through `run_prototype`.
- Company events streamed back into Discord.
- Approval messages with Approve, Reject, and Request Changes controls.
- Commands for audit logs and company dashboard/status.
- Conversation consolidation into project memory on disconnect.
- Recovery of pending approvals after reconnect/ping.

Typical dependency install:

```bash
pip install discord.py
```

The onboarding flow can prompt for a Discord bot token and save it with the rest of the local setup configuration.

---

## Memory and auditability

Aider Plus uses memory at multiple levels:

- **Conversation memory** stores recent chat/session context.
- **Project memory** stores durable project data in `.aider/project_memory.json` or configured memory locations.
- **Dream/consolidation memory** summarizes Discord session conversations into longer-lived project memory.
- **Repository memory** provides repository-aware memory primitives.
- **Audit log events** record department actions, approvals, deliverables, QA pass/fail, deployment status, and post-mortem activity.

Audit data is viewable from:

- Discord `audit` command.
- Discord `company_status` / `dashboard` commands.
- Desktop helper rendering via `render_desktop_audit_log`.
- Project memory JSON for direct inspection.

---

## GUI and desktop mode

Aider Plus keeps upstream browser GUI behavior and adds a desktop wrapper:

- `--gui` / `--browser` launches the Streamlit browser app.
- `--desktop` starts the same Streamlit app on a local port and hosts it in a native pywebview window.
- The desktop wrapper finds an available port, waits for the server, sets desktop-friendly Streamlit flags, cleans up the child process, and optionally starts a tray icon when dependencies are present.
- Default/minimum window sizes are tuned for a full coding workspace.
- GUI settings include OpenRouter API key support.

---

## Benchmarking, docs, and website

Aider Plus includes upstream Aider's docs and benchmarking assets:

- `benchmark/` contains benchmark/evaluation scripts, SWE-bench-related workflows, plotting, docker support, and leaderboard generation assets.
- `aider/website/` contains the documentation website, install/config/LLM docs, troubleshooting, examples, blog posts, benchmark writeups, and leaderboard data.
- Website docs were updated for GPT-5.5-related references and advanced model settings.

---

## Development

### Run tests

```bash
pytest -q
```

### Run targeted tests

```bash
pytest -q tests/basic/test_discord_integration.py
pytest -q tests/basic/test_company_orchestrator.py
pytest -q tests/basic/test_main.py
pytest -q tests/basic/test_models.py
```

### Helpful commands

```bash
python -m aider --help
python -m aider --headless --msg "Summarize this repository"
python -m aider --desktop
```

### Important repo locations

- Core package: `aider/`
- Agent runtime: `aider/agent/`
- Company workflow: `aider/company/`
- Integrations: `aider/integrations/`
- Memory: `aider/memory/`
- Tests: `tests/`
- Benchmarks: `benchmark/`
- Website/docs: `aider/website/`
- Utility scripts: `scripts/`

---

## Safety model

Aider Plus follows a practical safety posture for code agents:

- Agent iterations are bounded.
- Headless mode is explicit and intended for controlled environments.
- Department tool permissions can block unauthorized tool use.
- Human approvals can block PRD and release handoffs.
- Repository policies can restrict Discord-triggered work to approved roots.
- Prompt size and runtime limits protect bot integrations.
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
- better benchmark coverage for end-to-end delivery workflows.

---

## Upstream Aider docs

Because Aider Plus is a fork, upstream Aider documentation remains useful for the base editing engine, CLI behavior, model configuration, repo maps, lint/test hooks, and general usage:

- Install docs: https://aider.chat/docs/install.html
- Usage docs: https://aider.chat/docs/usage.html
- LLM/provider docs: https://aider.chat/docs/llms.html
- Config docs: https://aider.chat/docs/config.html
- Git docs: https://aider.chat/docs/git.html
- Website source in this repo: `aider/website/`

### Aider Plus commit additions summary

The following summarizes each non-merge Aider Plus commit visible in this branch's history, in chronological order:

| Commit | What it added or changed |
| --- | --- |
| `308b154` | Added GPT-5.5 model settings/aliases across providers and test coverage for those model settings. |
| `c723364` | Added advanced model settings documentation and refreshed FAQ/sample analytics content. |
| `3ec8ec5` | Updated FAQ token percentage references and switched the history update script's model reference to GPT-5.5. |
| `e56bd79` | Added the first headless mode defaults and Discord integration scaffolding. |
| `531da4b` | Documented headless mode and Discord integration support in the README. |
| `b58b443` | Reframed the README around Aider Plus's agent-first direction. |
| `22f3b87` | Split agent-loop context construction into an explicit, testable build step. |
| `79219d2` | Switched agent context caching toward coder-native message formatting. |
| `847d109` | Preserved Aider prompt caching behavior when agent context messages are generated. |
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
| `3b19a1e` | Rewrote the README with a full Aider Plus capabilities overview. |
| `faa0d76` | Added a README summary of Aider Plus commit additions. |
| `e2c9ef7` | Added desktop app mode that wraps the browser GUI in a native desktop window. |
| `41382aa` | Improved desktop GUI defaults, lifecycle handling, process cleanup, and related UX. |
| `bf3a772` | Added an OpenRouter API key field to GUI settings. |
| `19f31a7` | Fixed EngineeringDepartment handoff into the agent loop. |
| `275507b` | Added ProductDepartment and a Discord prototype flow for PRD-first feature requests. |
| `bd6fbe9` | Routed generated PRD context through company handoffs so Engineering receives structured requirements. |
| `d461fde` | Added blocking PRD approval handoff before Product work moves to Engineering/UX. |
| `f867288` | Added a project state machine to the orchestrator. |
| `fe4823d` | Persisted company approval gates in project memory. |
| `6014143` | Added department tool permissions and QA test execution/reporting. |
| `53cec2a` | Added DevOps and UX stages to the company delivery pipeline. |
| `f98a6e5` | Routed approved releases to DevOps instead of stopping at QA approval. |
| `a8a55ed` | Added company audit logging and a post-mortem playbook path. |
| `5a6191c` | Stabilized company core interfaces for tasks, deliverables, events, and state. |
| `e1341b1` | Centralized lifecycle approval handling and audit-log viewing. |
| `782b1b9` | Stabilized company workflow boundaries, handoffs, and communication between departments. |

Merge commits are intentionally omitted from the table because they primarily integrate the feature commits above.
