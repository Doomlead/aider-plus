<p align="center">
  <a href="https://aider.chat/"><img src="https://aider.chat/assets/logo.svg" alt="Aider Logo" width="280"></a>
</p>

<h1 align="center">Aider Plus</h1>

<p align="center">
<strong>Aider Plus</strong> is an agent-first fork of <a href="https://github.com/Aider-AI/aider">aider-chat</a>. It keeps Aider's git-aware coding engine and layers on headless automation, an autonomous tool-calling runtime, a Product → UX → Engineering → QA → DevOps delivery workflow, structured PRD and DesignSpec handoffs, UX schema gates, engineering reviewer loops, human approval gates, Discord/browser/desktop surfaces, persistent project memory, audit logs, observability, retrieval-aware context, prompt-caching controls, and post-mortem learning.
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
- [Prompt caching and department configuration](#prompt-caching-and-department-configuration)
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

1. Gather repository state, repo maps, conversation memory, repo-scoped project memory, user instructions, workflow state, model metadata, retrieval-ranked project artifacts, relevant playbook lessons, and department-specific runtime configuration.
2. Decide whether to answer directly, run one headless task, call agent tools, ask for clarification, or route the request through a structured delivery workflow.
3. Use Aider's coder implementations for real edits, diffs, lint/test hooks, commits, repo-map context, prompt caching, model-specific prompting, and git-native reviewability.
4. Coordinate larger work through Product, UX, Engineering, QA, DevOps, approval gates, audit logs, bounded revision loops, post-mortems, and persistent task status.
5. Learn from prior sessions by extracting typed audit patterns, deduplicating and bounding a playbook, retrieving only relevant lessons, and tracking cross-run task/QA/token/cache metrics.
6. Expose the same core runtime to terminal commands, scripts, Discord bots, Streamlit/browser UI, native desktop windows, and future service adapters.

In short: this repository is both an interactive AI pair-programmer and an embeddable runtime for autonomous software agents that can plan, implement, review, approve, test, release, observe, and improve from historical outcomes.

---

## What this repo now does

Aider Plus now combines upstream Aider with a multi-surface autonomous delivery runtime. In practical terms, this checkout can:

### Preserve the upstream Aider coding experience

- Run classic interactive Aider chat against selected files, repo maps, git state, lint/test commands, URL content, images, voice/watch/copy-paste flows, and Aider's edit formats.
- Use upstream coder modes including ask, code, architect, editblock, whole-file, unified-diff, and patch-style editing.
- Keep git-native reviewability through tracked diffs, optional auto-commits, commit messages, dirty-file checks, `.aiderignore`, read-only context, and no-git workflows.
- Carry upstream documentation, website, benchmark, leaderboard, history, model-alias, and requirements/constraint assets forward in this fork.

### Support current model/provider metadata

- Ship refreshed model settings and aliases for OpenAI GPT-5.x, GPT-5 Codex, GPT-5.1/5.2/5.3/5.4/5.5, GPT-5 Pro, Claude Sonnet/Opus 4.x, Gemini 2.5/3, DeepSeek, OpenRouter, Bedrock, Vertex, and related variants.
- Handle provider-specific behavior such as Claude 4 temperature disabling, `thinking_tokens` gating, reasoning-effort defaults, overeager prompting, OpenRouter aliases, Bedrock/Vertex entries, and updated model-test expectations.
- Expose OpenRouter setup prominently through onboarding and the GUI settings panel.

### Run as a headless or embeddable agent

- `--headless` and `--bot-mode` configure non-interactive defaults for queues, scripts, CI, Discord workers, and service wrappers by disabling rich/streaming terminal UI and auto-approving prompts.
- `AiderAgentLoop` builds structured context from coder state, repo information, recent conversation, project memory, prior coder results, and project instructions.
- Agent calls can use LiteLLM-compatible tool definitions, bounded tool iterations, prompt-cache metadata, coder-native message assembly, and structured result metadata.
- Coding tool calls execute through Aider-backed architect/editor orchestration so planning and editing can be separated while still using normal Aider file editing.
- `ToolRegistry` centralizes tool registration, tool dispatch, department-aware allowlists, and structured permission failures.

### Orchestrate Company Mode delivery

- `CompanyOrchestrator` routes work through a software-company workflow with project lifecycle state, department registration, handoffs, approval gates, background task management, audit events, status/dashboard data, and post-mortem learning.
- Product performs LLM ambiguity detection, creates CEO clarification gates with targeted questions, resumes PRD drafting from human answers, generates typed PRDs, self-reviews requirements quality, and preserves both Markdown and structured PRD data.
- UX consumes Product context and emits strict `DesignSpecV2` artifacts with screens, routes, components, data contracts, interaction states, accessibility checklists, global state guidance, and error-boundary notes.
- UX output passes through an Engineering-owned schema gate that validates JSON structure, forbids unknown fields, checks component/screen references, requires loading and error states, retries once with rejection feedback, and blocks invalid design handoffs.
- Engineering receives PRD, structured design-spec, schema-gate, QA, and playbook context, runs programmer/reviewer sub-phases, extracts structured review feedback, injects revision feedback into follow-up programmer prompts, and records changed files, diffs, commits, review state, and cache usage.
- QA runs targeted checks, records pass/fail/no-test outcomes, produces structured QA feedback, and can reroute failures back to Engineering for bounded fixes.
- Release approval can block deployment, and DevOps records deployment/release completion once release gates pass.
- Departments remain isolated behind task/deliverable interfaces and tool allowlists instead of directly mutating each other's state.

### Persist memory, retrieval, learning, and metrics

- Conversation memory stores session context for bot and long-running interactions.
- Dream consolidation summarizes older Discord/session history to keep long-running conversations compact.
- Repo-scoped project memory stores schema version, company project state, pending approvals, audit logs, deliverables, deployment/release data, playbooks, post-mortems, token/cost/cache metrics, QA metrics, and task metrics.
- Memory migrations normalize older project-memory files into the current schema.
- TF-IDF retrieval ranks project artifacts and playbook lessons so departments receive relevant context instead of the entire memory blob.
- Post-mortems extract typed audit patterns, deduplicate lessons, bound playbook size, and make future workflows retrieval-aware.
- Observability records department token/cost usage, cache-enabled versus uncached runs, task outcomes, QA pass/fail/no-test counts, audit trends, and lifecycle bottlenecks.

### Expose multiple user surfaces

- Terminal: classic Aider, headless tasks, `aider onboard`, `aider init`, `aider approve <gate-id>`, `aider reject <gate-id> [reason]`, browser mode, and desktop mode.
- Discord: headless engineering tasks, Company prototype flow, Product clarification/PRD approvals, release approvals, audit/status views, and session memory consolidation.
- Browser GUI: Streamlit chat, model/key settings, OpenRouter key field, Company Mode routing, dashboard/status tabs, approval handling, audit-log inspection, project-memory display, and direct Aider actions.
- Desktop GUI: pywebview wrapper around the browser GUI with local Streamlit lifecycle management, cleanup, optional debug/devtools, tray support when available, and Company workflow controls.
- Python API: importable agent, company, memory, retrieval, playbook, and integration classes for embedding this runtime in other services.

---

## Core capabilities

### Editing and coding

Aider Plus keeps Aider's core loop: it discovers repository context, builds repo maps, sends model-specific prompts, edits files with chosen edit formats, runs lint/test commands, tracks costs/tokens, and optionally commits changes. Aider Plus adds agent-oriented entry points around that engine rather than replacing it.

### Agent loop

The agent loop can run a single autonomous task, construct a context bundle, call a model with tool definitions, authorize and execute the `aider_coder` tool, and return structured outcomes. It supports prompt-caching controls that can be toggled globally, per call, and through Company department configuration.

### Company-style delivery

The Company runtime models a small software organization:

- **Product** checks ambiguity, asks targeted clarification questions when needed, resumes with CEO answers, turns clear requests into typed/self-reviewed PRDs, and preserves Markdown plus structured PRD dictionaries.
- **Clarification approval** blocks PRD drafting until a human supplies answers; **PRD approval** can block downstream UX/Engineering work until a human approves, rejects, or requests changes.
- **UX** produces schema-validated `DesignSpecV2` handoffs from Product context, including screens, components, data contracts, interaction states, accessibility, state management, and error handling.
- **Schema gate** validates UX deliverables before Engineering receives them, retries once with actionable rejection feedback, and returns a `validation_failed` deliverable if the design remains incomplete or inconsistent.
- **Engineering** implements from PRD/design/schema-gate context using programmer/reviewer sub-phases, bounded revision prompts, structured reviewer feedback, and reviewer safeguard metrics.
- **QA** runs checks, records pass/fail/no-test outcomes, and can route structured feedback back to Engineering.
- **Release approval** can block deployment.
- **DevOps** records deployment or release completion.
- **Post-mortem learning** extracts patterns from outcomes and adds only novel, bounded, typed lessons to future playbooks.

### Surfaces

- **Terminal**: direct Aider CLI, `--headless`, `--bot-mode`, `--desktop`, onboarding commands, and approval commands.
- **Discord**: bot façade for headless engineering tasks, prototype flows, approvals, audit viewing, company status, and memory consolidation.
- **Browser GUI**: Streamlit chat UI with model/key settings, OpenRouter key affordances, Company Mode controls, dashboard tabs, approvals, audit log, and project-memory display.
- **Desktop GUI**: native wrapper around the browser GUI with local-process lifecycle management and optional debug/devtools support.
- **Python APIs**: `AiderAgentLoop`, `AgentLoopConfig`, `ToolRegistry`, `CompanyConfig`, `DepartmentConfig`, `CompanyOrchestrator`, department classes, `ProjectMemory`, `ContextBuilder`, `MemoryRetriever`, `PlaybookManager`, `AuditPatternExtractor`, Discord/session helpers, and desktop/browser helpers.

## Architecture overview

Important runtime areas:

- **Aider core**: `aider/` contains the upstream coding engine, coders, model metadata, CLI, commands, repo map, IO, git integration, browser GUI, desktop launcher, and supporting utilities.
- **Agent loop**: `aider/agent/` contains tool definitions, the department-aware `ToolRegistry`, prompt-cache-aware agent calls, structured reviewer calls, and the Aider-backed agent loop.
- **Company workflow**: `aider/company/` contains the orchestrator, state manager, lifecycle transitions, departments, approval gates, department configuration, context builder, audit helpers, schemas, UX schema validators, and playbook manager.
- **Memory**: `aider/memory/` contains conversation memory, project memory, dream consolidation, repository memory, the TF-IDF retriever, and audit pattern extraction.
- **Integrations**: `aider/integrations/` contains the Discord adapter.
- **GUI and desktop**: `aider/gui.py` and `aider/desktop.py` expose direct chat and Company Mode through browser and native desktop paths.
- **Tests**: `tests/company/` contains the focused company workflow, permissions, prompt-caching, playbook/pattern, UX schema-gate, Discord lifecycle, and engineering-review tests.
- **Docs/site/benchmarks**: `aider/website/`, `benchmark/`, root docs, histories, and requirement files retain upstream assets plus Aider Plus updates.

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
        |       |
        |       +--> optional cache_control metadata + structured reviewer calls
        |
        +--> CompanyOrchestrator
                |
                +--> CompanyConfig / DepartmentConfig
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
- Python 3.14 support is marked experimental in this branch.

---

## Configuration and model providers

Aider Plus inherits upstream Aider configuration behavior:

- CLI flags, `.aider.conf.yml`, environment variables, and model/provider settings continue to work.
- Model settings are defined in `aider/resources/model-settings.yml` and provider handling is in `aider/models.py`.
- OpenRouter support is emphasized in onboarding and GUI settings.
- This branch includes model metadata additions for GPT-5.x/GPT-5.5, Claude 4.x, Gemini 2.5/3, and newer provider variants.
- Advanced model settings docs and sample analytics assets are refreshed in the bundled website source.

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

loop = AiderAgentLoop(coder=coder, enable_prompt_caching=True)
result = await loop.run("Add validation and tests for the payment payload")
```

### 4) Company prototype flow

Use `CompanyOrchestrator`, Discord `/prototype`, or Company Mode in the GUI to route a raw product idea through Product ambiguity detection, optional CEO clarification, typed PRD creation, PRD approval, UX/design, Engineering implementation, reviewer revisions, QA, release approval, DevOps, and post-mortem learning.

### 5) Retrieval-aware playbook learning

Let Company workflow runs accumulate audit logs. Post-mortems extract typed lessons into the playbook, `PlaybookManager` deduplicates and bounds them, and `ContextBuilder` injects only relevant lessons for the next task.

### 6) Desktop Company Mode

Start `aider --desktop`, enable Company Mode, choose Auto/Prototype/Engineering routing, watch the dashboard, approve or reject gates, inspect audit events, and view project memory without leaving the desktop app.

---

## Company workflow

The Company system is centered on typed interfaces, validation gates, and persisted state:

- `CompanyTask`: normalized work request with task id, origin/target departments, artifact type, payload, blocking flag, and context.
- `Deliverable`: department output with status, payload/content alias, metadata, task id, artifact type, and department name.
- `PRD` and `ClarificationRequest`: typed Product artifacts for structured requirements, Markdown previews, clarification questions, and approval recovery.
- `DesignSpec` / `DesignSpecV2`: typed UX artifacts for screens, routes, components, data contracts, interaction states, accessibility checklists, global state management, error boundaries, and Markdown/JSON handoffs.
- `SchemaGateValidator` and `GateResult`: Engineering-owned UX validation gate that parses strict `DesignSpecV2` JSON, reports field and semantic errors, and produces rejection payloads for retry or blocked handoff.
- `QAFeedback`: structured QA-to-Engineering failure context with failing tests, output excerpts, covered files, recommendations, revision number, and PRD excerpts.
- `CompanyEvent`: lifecycle/audit event emitted by departments and the orchestrator.
- `Department`: base interface for context requirements, tool allowlists, and task handling.
- `DepartmentConfig`: per-department prompt caching and preferred model settings.
- `CompanyConfig`: top-level orchestration config for department overrides, default cache behavior, and cache-stat recording.
- `CompanyStateManager`: persistence and recovery wrapper around `ProjectMemory` plus observability and playbook helpers.
- `CompanyLifecycle`: phase transition rules.
- `ApprovalManager`: blocking approval creation, persistence, recovery, and resolution.
- `ContextBuilder`: retrieval-aware context construction for department tasks.
- `PlaybookManager`: bounded, deduplicated, retrieval-queryable lessons learned.
- `CompanyOrchestrator`: submit/route tasks, register departments, emit events, coordinate handoffs, track metrics, apply config, and run post-mortems.

### Lifecycle overview

1. A user submits a raw idea or task.
2. Product checks whether the request is clear enough to write a PRD.
3. If the request is ambiguous, Product opens a clarification approval gate so a human can answer targeted questions before PRD drafting resumes.
4. Product generates a typed PRD, converts it to Markdown for review, self-reviews quality, and stores structured PRD metadata for downstream handoffs.
5. PRD approval can block work until a human approves, rejects, or requests changes.
6. UX produces a structured `DesignSpecV2` handoff when the project requires design.
7. The schema gate validates UX JSON structure, required fields, component state coverage, and screen-to-component references; UX gets one automatic retry with gate feedback before the handoff is marked `validation_failed`.
8. Engineering receives relevant PRD/design/schema-gate/playbook context and runs a programmer/reviewer implementation loop.
9. QA runs checks and records pass, fail, or no-test outcomes.
10. Failed QA can send structured feedback back to Engineering for bounded revision cycles.
11. Release approval can block deployment.
12. DevOps records deployment/release completion.
13. Post-mortem handling records outcomes, extracts audit patterns, updates the deduplicated playbook, and advances final lifecycle state.

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
- **Typed pattern extraction** converts QA failures, CEO/approval rejections, deployment failures, and Engineering reviewer revisions into reusable lessons.
- **Playbook management** deduplicates similar entries, caps each category, and supports retrieval of only relevant patterns for a given task.
- **Observability metrics** track turns per phase, token/cost usage per department, cached/uncached run counts, QA metrics, task completion/failure counts, task durations, and recent status for dashboards.

---

## Prompt caching and department configuration

Aider Plus preserves upstream prompt-cache behavior for normal coder usage and adds Company-level controls:

- `AiderAgentLoop(..., enable_prompt_caching=True|False)` controls the high-level `cache_prompts` flag for agent LLM calls and adds cache-control metadata where supported.
- `AiderAgentLoop.run(..., enable_caching=...)` and `run_structured(..., enable_caching=...)` can override caching per call without changing Aider `Coder` message formatting.
- `CompanyConfig.default_enable_caching` controls departments without explicit overrides; `get_department_config()` resolves department names case-insensitively.
- `DepartmentConfig.enable_prompt_caching` can toggle caching for Product, UX, Engineering, reviewer, QA, and DevOps independently.
- `DepartmentConfig.preferred_model` can steer department/reviewer calls toward a preferred model.
- The default company config enables caching for Product, UX, Engineering, and reviewer calls, while disabling it for smaller QA and DevOps calls.
- When `CompanyConfig.record_caching_stats` is enabled, project observability records cached and uncached runs per department.

Example:

```python
from aider.company.config import CompanyConfig, DepartmentConfig
from aider.company.orchestrator import CompanyOrchestrator

company_config = CompanyConfig(
    departments={
        "engineering": DepartmentConfig(
            name="engineering",
            enable_prompt_caching=True,
            preferred_model="claude-sonnet-4-5",
        ),
        "qa": DepartmentConfig(name="qa", enable_prompt_caching=False),
    },
)

orchestrator = CompanyOrchestrator(project_memory, company_config=company_config)
```

---

## Discord integration

Discord support is implemented as a headless integration layer:

- `DiscordAiderBot` can run headless Aider tasks for allowed repositories.
- `/prototype` starts Product-led ambiguity detection, optional clarification approval, typed PRD creation, and PRD approval flow.
- Engineering tasks run through the Company orchestrator rather than bypassing department boundaries.
- Approval buttons and modals let humans approve, reject, or request changes for clarification, PRD, and release gates.
- Pending approvals can be recovered after restart from project memory.
- Audit logs and company status can be surfaced in Discord.
- Conversation memory and dream consolidation keep bot sessions compact.
- Repo policies can restrict bot execution to known roots and enforce payload limits.

Minimal conceptual setup:

```python
from aider.integrations.discord import DiscordAiderBot, DiscordAiderConfig

bot = DiscordAiderBot(DiscordAiderConfig(token="...", default_model="gpt-5.5"))
bot.run()
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
- Approval pages provide approve, reject, and request-changes interactions, and the optional feedback field is used as the answer body for clarification approvals.
- Background workflow execution is isolated from the Streamlit request thread and exposes pending-run and error indicators.
- Model/settings UI includes OpenRouter API key handling.

---

## Model, docs, benchmark, and website assets

The repo retains upstream Aider's documentation, benchmark scaffolding, website source, histories, requirements, and model metadata while carrying Aider Plus updates:

- Website source and docs live in `aider/website/`.
- Benchmark tooling and results live in `benchmark/`.
- Model settings live in `aider/resources/model-settings.yml` and `aider/models.py`.
- Requirements and constraint files track the current local development/browser/test dependency set.
- This branch includes metadata/docs for GPT-5.5 plus newer OpenAI, Anthropic Claude, Gemini, DeepSeek, OpenRouter, Bedrock, and Vertex variants.
- Website docs include updated advanced model settings, aliases, FAQ, leaderboards, infinite-output guidance, and usage command references.
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
- UX schema-gate retries are bounded.
- QA-to-Engineering revision cycles are bounded.
- Headless mode is explicit and intended for controlled environments.
- Department tool permissions block unauthorized tool use before execution.
- Human approvals can block clarification, PRD, and release handoffs.
- Strict UX schema validation blocks incomplete or inconsistent designs before Engineering implementation.
- CLI, Discord, and GUI approval paths all persist through project memory.
- Repository policies can restrict Discord-triggered work to approved roots.
- Prompt size and runtime limits protect bot integrations.
- Retrieval-aware context avoids unbounded memory injection.
- Playbook categories are capped and deduplicated.
- Company prompt caching can be enabled/disabled by department to balance speed/cost against provider behavior.
- Background GUI tasks expose errors instead of silently failing.
- Persistent audit logs and observability make automated work inspectable over time.
- Git-native outputs keep diffs and commits human-reviewable.

---

## Roadmap direction

All three initial priority tiers are represented in this branch:

- **Short-term**: CLI approval resolution, strict tool-permission enforcement, execution-order safety, bootstrap support, and targeted permission tests.
- **Medium-term**: TF-IDF retrieval, retrieval-aware context injection, schema-v3 project memory migration, structured token/cost/task/QA/cache metrics, orchestrator metrics wiring, and dashboard/status output.
- **Long-term**: typed audit-log pattern extraction, retrieval-deduplicated bounded playbooks, post-mortem learning, context-builder playbook delegation, pattern/playbook tests, and department-level prompt-caching controls.

The next natural capability tier is **cross-session observability**: tooling that reads `task_metrics`, `qa_metrics`, token/cost/cache data, and audit trends across time to surface regressions, reliability drift, and process bottlenecks across repositories or repeated delivery sessions.

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


The following table summarizes every visible commit in this branch's current grafted history that materially added or changed behavior, in chronological order. It includes the grafted upstream baseline plus the upstream sync/model/docs/test/maintenance work carried by this fork and the Aider Plus agent, Company Mode, memory, GUI, Discord, approval, prompt-caching, Product, UX, Engineering, QA, DevOps, schema-gate, and learning work. Ordinary pull-request merge commits are omitted because they primarily integrate the feature commits listed here.

| Commit | What it added or changed |
| --- | --- |
| `5b0f6ce` | Imported the upstream Aider baseline into this fork, including the CLI, coders, repo-map engine, git integration, browser UI, docs/site, benchmark scaffolding, packaging, and tests. |
| `90ac33c` | Refreshed website docs, FAQ, model-alias docs, advanced model settings, sample analytics, and homepage copy. |
| `f626e44` | Updated leaderboard website content. |
| `f730853` | Allowed read-only files to be promoted into editable context when `git: false` disables git integration. |
| `4e77720` | Prevented staging files when auto-commits are disabled. |
| `7a1bd15` | Set the package version to `0.86.3.dev`. |
| `172df73` | Allowed files outside the repository to be added when git commits are off. |
| `4625ebb` | Added `verify_ssl=False` scraper setup for tests. |
| `b2bec25` | Fixed symlink-loop handling in `safe_abs_path()`. |
| `4b48d82` | Added `/ok` as an alias for `/code Ok`. |
| `d19a9b0` | Updated docs, FAQ, infinite-output guidance, usage command docs, and sample analytics around command behavior. |
| `edfe0c8` | Allowed `/ok` to accept optional arguments. |
| `f761d72` | Added the `overeager` model setting to GPT-4 Turbo. |
| `ec3470c` | Enabled `overeager` for GPT-5.2 Codex variants. |
| `37d6ebd` | Added overeager prompting to ask-mode prompts. |
| `265d8a4` | Refreshed README, website FAQ, advanced model settings, infinite-output docs, command docs, homepage copy, and sample analytics. |
| `975e5a8` | Marked Python 3.14 support as experimental. |
| `c0ab753` | Removed deprecated GPT-4 32k model expectations from tests. |
| `c0839cf` | Removed deprecated timestamped model expectations from tests. |
| `5516493` | Removed deprecated vision model expectations from tests. |
| `38716cc` | Added `ExInfo` details for `PermissionDeniedError`. |
| `0ec5f35` | Added test coverage for `PermissionDeniedError` exception info. |
| `07c526f` | Adjusted the `PermissionDeniedError` test to include a response argument. |
| `8955c4e` | Added the missing `PermissionDeniedError` import. |
| `c335682` | Fixed the `PermissionDeniedError` test to use `httpx` objects. |
| `413149e` | Removed an unused import from `test_exceptions.py`. |
| `5b038fd` | Bumped dependencies and refreshed README, FAQ, infinite-output docs, homepage copy, and sample analytics. |
| `fabdce1` | Added GPT-5.3 Codex model variants. |
| `c41ef3b` | Added GPT-5.3 and GPT-5.4 model variants. |
| `3c2a8bd` | Expanded advanced model settings docs and refreshed FAQ and sample analytics. |
| `bdb4d9f` | Updated history files, FAQ content, sample analytics, and history-update tooling. |
| `f09d706` | Enabled overeager mode for Claude Sonnet 4.5 models. |
| `928bb49` | Refreshed sample analytics data. |
| `f939d0a` | Added Claude Sonnet 4.6 and Claude Opus 4.7 model support. |
| `9ce34d1` | Simplified model-name conditional logic in `models.py`. |
| `b9d8774` | Mapped `opus` and `sonnet` aliases to the latest Claude models. |
| `79c45c3` | Disabled deprecated temperature handling for Claude 4 models. |
| `39023f9` | Disabled temperature for Opus 4 models and gated `thinking_tokens` behavior. |
| `93dfacc` | Added Claude Opus 4.7 settings for Bedrock, Vertex, and OpenRouter. |
| `65cb4d3` | Reformatted the `thinking_tokens` model check for readability. |
| `0189cf4` | Refreshed README, advanced model settings, model aliases, FAQ, infinite-output docs, homepage copy, and sample analytics. |
| `cd24a3a` | Updated model alias test expectations for Sonnet and Opus. |
| `308b154` | Added GPT-5.5 model settings across supported providers. |
| `c723364` | Expanded GPT-5.5-related advanced model settings and refreshed FAQ/sample analytics. |
| `3ec8ec5` | Updated FAQ token percentages and switched history references to GPT-5.5. |
| `e56bd79` | Added initial headless mode and Discord integration scaffolding. |
| `531da4b` | Documented headless mode and Discord integration support. |
| `b58b443` | Refactored the README around the Aider Plus agent-first direction. |
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
| `e517c98` | Refreshed the README to describe completed short-, medium-, and long-term Aider Plus capability tiers and updated the commit summary. |
| `c6ac28c` | Added Company prompt-caching controls, per-department cache configuration, preferred model hooks, and cached/uncached observability tracking. |
| `f69d7db` | Refreshed the README for Company prompt-caching controls and current Aider Plus behavior. |
| `1403bd0` | Added another pass of Company prompt-caching controls and per-department cache configuration. |
| `f1219c2` | Added structured Product clarification workflow with ambiguity detection, CEO questions, typed PRDs, and self-review. |
| `e451dc2` | Handled clarification approval responses so Product can resume PRD generation from human answers. |
| `c0d1e8a` | Added structured UX design specs with Markdown/JSON handoffs for Engineering. |
| `6f9a4e8` | Integrated structured PRD and design-spec context into Engineering prompts. |
| `1167f51` | Completely refreshed `README.md` to describe the current Aider Plus runtime and updated the commit-additions summary through the Product, UX, and Engineering changes available at that point. |
| `b2a2fcc` | Hardened Engineering review context handoffs and added regression tests for reviewer context propagation. |
| `5172ae9` | Strengthened Engineering reviewer handoff metadata, metrics, config controls, and orchestrator integration. |
| `e51ce8f` | Improved Engineering review context handoff formatting and orchestration behavior. |
| `4eeb86c` | Polished Engineering reviewer safeguards, Discord/GUI lifecycle display behavior, audit-pattern extraction, and related tests. |
| `94fc2b1` | Refined Engineering reviewer phase controls and reduced fragile review-loop behavior. |
| `bdc5922` | Added the UX design schema gate with strict `DesignSpecV2` models, schema validation, semantic checks, blocked handoff payloads, and tests. |
| `3cdd37a` | Propagated UX schema-gate context through orchestrator handoffs so Engineering can see validation status and structured design data. |
| `9640f2f` | Wired UX schema-gate retry flow so invalid design specs get one automatic regeneration attempt with rejection feedback. |
| `cbbe387` | Added tests for UX schema-gate retry handling. |
| `97b1cf7` | Hardened UX structured-output parsing and fallback behavior, with coverage for JSON-in-string responses. |
| `(this commit)` | Updated this README to cover the current Aider Plus runtime end to end, including UX `DesignSpecV2` schema gates, schema-gate retry behavior, richer Engineering review handoffs, and the latest commit-additions summary. |
