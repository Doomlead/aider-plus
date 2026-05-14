# Aider Plus

Aider Plus is a hackable, agent-first coding workspace built around a deliberately small and readable core. It keeps Aider's git-aware pair-programming loop, then adds a Nanobot-inspired COO, browser and desktop GUIs, project memory, MCP/tool paths, approvals, deployment handoffs, and research-friendly agent-loop boundaries.

The product goal is simple: messages can arrive from any chat app, GUI, CLI, API, or MCP surface; the COO and machine-learning agent loop decide whether a direct answer, memory recall, tool call, or company handoff is needed; memory and skills are pulled in as context; and product-building or iteration requests are forwarded to the orchestrator instead of being reimplemented in each surface.

---

## Feature list

### Small, readable core

- **Chat surfaces stay thin.** Browser, desktop, CLI, API, MCP, and Discord normalize user messages and session identity, then hand off to shared runtime code.
- **One COO path.** `NanobotCOO` owns CEO-facing action decisions, durable sessions, memory, status inspection, and handoff into Company Mode.
- **One orchestrator path.** `CompanyOrchestrator` remains the canonical workflow engine for Product → UX → Engineering → Reviewer → QA → DevOps.
- **Composable departments.** Each department has a focused Aider agent loop, schema/context expectations, and audit-friendly deliverables.
- **Hackable files.** The main implementation lives in ordinary Python modules under `aider/company/`, `aider/memory/`, `aider/mcp/`, `aider/gui.py`, and `aider/desktop.py`.

### Research-readiness

- **Explicit decision objects.** `COOActionDecision` and `COORouteDecision` make routing, confidence, reasoning, escalation, and target selection inspectable.
- **Deterministic fallback.** The COO can answer common personal-assistant requests locally and fall back to deterministic department routing when LLM routing is disabled or unavailable.
- **LLM-routing switch.** `CompanyConfig.enable_coo_llm_routing` can enable richer COO decisions using history, memory, status, and available departments.
- **Audit and lifecycle events.** The dashboard, audit log, and lifecycle formatter expose what happened, who handled it, and which gate or deliverable changed.
- **Prompt-caching controls.** Per-agent model and caching settings can be configured consistently across the browser and desktop GUIs.

### Built-in chat, API, memory, MCP, and deployment paths

- **Browser GUI.** Streamlit interface with chat targets, settings, dashboard, approvals, audit log, project memory, and guide pages.
- **Desktop GUI.** Zero-dependency Tkinter app with the same Company workflow, settings, approvals, audit, dashboard, and per-agent chat targets.
- **Classic chat.** Direct Aider chat remains available for focused pair-programming outside Company Mode.
- **Headless/API use.** `--headless`/`--bot-mode` can be used by scripts, queues, service wrappers, and API shells.
- **MCP support.** MCP configuration, server status, and approval-gated tools plug into the same Company approval model.
- **Deployment handoff.** DevOps receives release/deployment tasks from the orchestrator instead of chat adapters owning deployment behavior.
- **Project memory.** Conversation memory, project memory, pattern extraction, COO memory, and audit history persist repo-local context.
- **Discord as chat only.** Discord is intentionally stripped back to a chat adapter; status, approvals, dashboards, audit, lifecycle, and product workflow live in the shared GUI/Company layers.

### Hackability

- Start from `aider/company/coo.py` when changing COO decisions, memory, status, and delegation.
- Start from `aider/company/orchestrator.py` when changing workflow handoffs, approvals, routing, and lifecycle behavior.
- Start from `aider/company/departments/` when changing Product, UX, Engineering, Reviewer, QA, or DevOps behavior.
- Start from `aider/gui.py` and `aider/desktop.py` for browser and desktop presentation.
- Start from `aider/integrations/discord.py` for the intentionally thin Discord chat adapter.

---

## Architecture

```text
User / CEO
  |
  v
Chat app, Browser GUI, Desktop GUI, CLI, API, or MCP surface
  |
  v
Message envelope + session identity
  |
  v
NanobotCOO personal assistant loop
  |  - reads durable COO session history
  |  - pulls COO profile, project memory, and skills into context
  |  - decides whether tools/MCP/status/memory are needed
  |  - answers, clarifies, remembers, recalls, inspects, or delegates
  |
  +--> direct CEO response / clarification / memory update / status brief
  |
  v
Company bridge: delegate_company_task
  |
  v
CompanyOrchestrator
  |
  v
Product -> UX -> Engineering -> Reviewer -> QA -> DevOps
  |
  v
Deliverables, audit log, approvals, memory, deployment output
```

The orchestrator is the only place that builds or iterates products. Chat surfaces should not implement their own Product, QA, approval, audit, status, or deployment logic. They send messages in and render shared results out.

---

## Install

```bash
python -m pip install -e '.[browser]'
```

For headless or desktop-only work, the core package can run without Streamlit. The browser GUI requires the `browser` extra; the desktop GUI uses Tkinter from the Python standard library.

---

## Quickstart

### Classic Aider chat

```bash
aider --model gpt-5.5
```

### Run one headless task

```bash
aider --headless --model gpt-5.5 --msg "Refactor the parser and add tests"
```

`--headless` also works as `--bot-mode` for scripts, queues, service wrappers, chat workers, and CI jobs.

### Start the browser GUI

```bash
aider --browser
```

Open the Chat, Settings, Company Dashboard, Approvals, Audit Log, Project Memory, and Guide tabs. The browser GUI exposes the shared Company workflow and all approval/status/audit functionality that used to be scattered across integration layers.

### Start the native desktop app

```bash
aider --desktop
```

The desktop app uses Tkinter only. It does not require Streamlit, pywebview, WebView2, or a browser. It exposes the same Company workflow, approvals, audit log, dashboard, settings, and per-agent chat paths as the browser GUI.

### Start onboarding

```bash
aider onboard
# or
aider init
```

---

## Chat targets in both GUIs

- **Direct Aider** — classic git-aware pair-programming.
- **Company Workflow** — COO-led Product → UX → Engineering → Reviewer → QA → DevOps orchestration.
- **COO** — CEO briefing, status, memory, clarifications, routing, and delegation.
- **Product** — requirements, ambiguity checks, PRDs, and launch criteria.
- **UX** — flows, states, accessibility, interaction details, and design specs.
- **Engineering** — implementation plans and code changes.
- **Reviewer** — implementation review and quality checks.
- **QA** — test plans, validation, release confidence, and regression guidance.
- **DevOps** — release, deployment, MCP/ops, rollback, and recovery guidance.

The Quick Agent Switcher and target selector let you jump between these roles without changing code paths.

---

## Settings and configuration

Both browser and desktop use the shared settings manager. Settings are organized into:

1. **Global Aider** — main, weak, and editor model defaults for direct chat.
2. **Per-Agent Overrides** — model, prompt caching, API key override, and local endpoint/setting for COO, Product, UX, Engineering, Reviewer, QA, and DevOps.
3. **Provider Keys** — OpenAI, Anthropic, OpenRouter, and other provider credentials.
4. **Advanced (.env + .aider.conf)** — extra `KEY=value` environment lines and raw `.aider.conf.yml` editing.

Use **Preview changes** before saving. Then use **Apply & Restart Company Session** so new agent loops pick up model, key, caching, `.env`, and `.aider.conf.yml` updates.

Example environment overrides:

```bash
AIDER_COMPANY_AGENT_MODELS="coo=gpt-5.5,product=gpt-5.5,engineering=gpt-5.5"
AIDER_COMPANY_MODEL_COO=gpt-5.5
AIDER_COMPANY_CACHING_COO=true
AIDER_COMPANY_CACHING_ENGINEERING=true
AIDER_MCP_ENABLED=true
```

---

## Memory, skills, and context

Aider Plus keeps memory local and inspectable:

- Conversation memory consolidates chat context into project memory.
- Project memory stores workflow state, audit data, playbook patterns, and recent outcomes.
- COO memory stores durable CEO preferences and notes under `.aider/coo/`.
- Skills and playbook guidance can be pulled into department context before a task is executed.
- MCP tool requests can be approval-gated and surfaced in the GUI approval flows.

This keeps the machine-learning loop research-friendly: inputs, memory, tool decisions, routes, and outputs are all observable.

---

## Discord integration

Discord is now only a chat app adapter. It can accept chat text and forward it to headless Aider, but it no longer owns Company Mode dashboards, approvals, audit logs, COO status commands, lifecycle buttons, or product workflow behavior. Use the browser GUI, desktop GUI, CLI, API, or MCP surfaces for those capabilities.

This makes Discord easy to replace with Slack, Matrix, webhooks, or custom chat apps: implement message normalization and session identity, then hand the message to the shared runtime.

---

## MCP support

MCP can be enabled for external tools and context. When enabled, dashboards report MCP status and configured server count. MCP tool approvals route through Company approval gates so human review remains visible in the GUI.

---

## Development

Useful checks:

```bash
python -m py_compile aider/gui.py aider/desktop.py aider/integrations/discord.py aider/company/coo.py
python -m pytest tests/company/test_coo_agent_framework.py tests/company/test_discord_lifecycle.py tests/company/test_product_department.py
```

Architecture map:

- `aider/company/coo.py` — Nanobot-style COO sessions, action decisions, memory, status, bus, and delegation.
- `aider/company/orchestrator.py` — workflow, approvals, lifecycle, context, handoffs, and audit coordination.
- `aider/company/departments/` — Product, UX, Engineering, Reviewer, QA, and DevOps implementations.
- `aider/company/surface_messages.py` — shared lifecycle/status/approval/audit message formatting for surfaces.
- `aider/memory/` — conversation memory, project memory, retrieval, consolidation, and pattern extraction.
- `aider/mcp/` — MCP configuration, manager, adapters, and server helpers.
- `aider/gui.py` — Streamlit browser GUI.
- `aider/desktop.py` — Tkinter desktop GUI.
- `aider/integrations/discord.py` — thin Discord chat adapter.
- `docs/company/nanobot_coo_architecture.md` — COO architecture details.
- `tests/company/` and `tests/mcp/` — focused coverage for Company workflow, COO behavior, GUI settings, approvals, lifecycle formatting, and MCP.

---

## Safety note

Aider Plus is experimental software. Use git branches, review generated changes, keep approval gates enabled for important work, and treat MCP/deployment actions as operations that need human oversight.
