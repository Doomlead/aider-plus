# Aider Plus

Aider Plus is an agent-first fork of Aider that keeps the git-aware pair-programming workflow and adds a polished browser GUI, a zero-dependency Tkinter desktop app, and an internal software-company runtime led by a Nanobot-style COO.

The current focus is making both GUIs feel like the same product: shared settings, clearer agent chat, stronger observability, safer previews before saving configuration, and prominent restart/apply feedback for Company sessions.

---

## What is new

### Identical settings in browser and desktop

Both Streamlit and Tkinter now use the same shared settings manager, so validation, previews, and persistence behave consistently.

Settings are organized into four clean sections:

1. **Global Aider** — main, weak, and editor model defaults for direct Aider chat.
2. **Per-Agent Overrides** — model, prompt caching, API key override, and local endpoint/setting for COO, Product, UX, Engineering, Reviewer, QA, and DevOps.
3. **Provider Keys** — OpenAI, Anthropic, OpenRouter, and other provider credentials.
4. **Advanced (.env + .aider.conf)** — extra `KEY=value` environment lines and raw `.aider.conf.yml` editing.

Before saving, use **Preview changes** to validate required fields, malformed `.env` lines, caching values, masked secret updates, and the merged `.aider.conf.yml` output. Use the prominent **Apply & Restart Company Session** button to persist settings, apply environment updates, and restart Company agent loops so new chats pick up the updated configuration.

### Better per-agent chat

Aider Plus exposes these chat targets in both GUIs:

- **Direct Aider** — classic Aider pair-programming.
- **Company Workflow** — COO-led Product → UX → Engineering → Reviewer → QA → DevOps orchestration.
- **COO** — status, memory, routing, delegation, and CEO-facing assistance.
- **Product** — requirements, clarification, PRDs, and product risk.
- **UX** — designs, states, accessibility, and interaction details.
- **Engineering** — implementation plans and code changes.
- **Reviewer** — implementation review and quality checks.
- **QA** — test plans, validation, and release confidence.
- **DevOps** — release, deployment, MCP/ops, and recovery guidance.

The browser GUI adds agent icons, clearer user-vs-agent message styling, code-block rendering, COO last-action/memory summaries, and a global **Quick Agent Switcher**. The desktop GUI adds a **Quick Agent Switcher**, cleaner chat navigation, separate transcripts per target, code-block styling, and Ctrl/Cmd+Enter send shortcuts.

### Dashboard and observability polish

The Company Dashboard now starts with a **System Overview** panel showing:

- Which agents have prompt caching enabled.
- Current COO status and last action.
- Pending human escalations/approvals.
- Active warehouse product registry, when present.
- MCP status and server count when MCP is enabled.

The COO Activity area is more readable, with collapsible Streamlit sections and desktop highlighting for COO errors/escalations. Recent deliverables, project phase, active runs, audit log entries, and raw company status remain available for deeper debugging.

### Quick UX wins

- Shared validation and recovery suggestions in settings.
- Better error display in chat and dashboard paths.
- Useful, short guide content instead of overwhelming reference dumps.
- Dark/light-compatible Streamlit defaults and cleaner Tkinter theme styling.
- Keyboard send shortcuts in desktop.
- Session/warehouse visibility in dashboards when multi-product support is active.

---

## Install

```bash
python -m pip install -e '.[browser]'
```

For headless or desktop-only work, the core package can run without Streamlit, but the browser GUI requires the `browser` extra.

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

`--headless` also works as `--bot-mode` and is intended for scripts, queues, service wrappers, Discord workers, and CI jobs.

### Start the browser GUI

```bash
aider --browser
```

Use the main tabs for Chat, Settings, Company Dashboard, Approvals, Audit Log, Project Memory, and Guide.

### Start the native desktop app

```bash
aider --desktop
```

The desktop app uses Tkinter only. It does not require Streamlit, pywebview, WebView2, or a browser.

### Start onboarding

```bash
aider onboard
# or
aider init
```

---

## Company Mode workflow

Company Mode turns a request into a managed product-building workflow:

```text
CEO/User
  ↓
Nanobot COO
  ↓
Product → UX → Engineering → Reviewer → QA → DevOps
  ↓
Audit log + Project Memory + Playbook learning
```

The COO can answer directly, ask for clarification, remember preferences, inspect status, delegate to a department, recover from route errors, or raise human escalations. Approval gates can be handled in the browser GUI, desktop GUI, or CLI.

---

## Configuration files

Aider Plus uses repo-local configuration by default:

- `.env` stores provider keys and Company agent overrides.
- `.aider.conf.yml` stores Aider model/configuration defaults.
- Project memory and audit data are stored through Aider Plus memory helpers.
- Warehouse product registries live under `products/warehouse.json` by default.

Common environment keys include:

```env
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
OPENROUTER_API_KEY=...
AIDER_COMPANY_MODEL_COO=gpt-5.5
AIDER_COMPANY_CACHING_ENGINEERING=true
AIDER_COMPANY_API_KEY_QA=...
AIDER_COMPANY_LOCAL_DEVOPS=http://localhost:11434
AIDER_MCP_ENABLED=true
```

Prefer the GUI Settings screen for day-to-day edits because it validates and previews changes before saving.

---

## MCP support

MCP can be enabled for external tools and context. When enabled, the dashboard reports MCP status and configured server count. MCP tool approvals are routed through Company approval gates so human review remains visible in the GUI.

---

## Warehouse and multi-product support

Aider Plus includes a thin warehouse registry for managing multiple Git-backed product repositories.

```bash
aider warehouse init
aider warehouse list
aider warehouse status
aider company new "Build a status page product" --name "Status Studio"
```

When a warehouse registry is present, dashboard System Overview surfaces active warehouse product information.

---

## Architecture map

Important runtime areas:

- `aider/gui.py` — Streamlit browser GUI.
- `aider/desktop.py` — Tkinter desktop GUI.
- `aider/gui_settings_manager.py` — shared GUI settings form, validation, preview, and save helpers.
- `aider/settings.py` — lower-level `.env` and `.aider.conf.yml` helpers.
- `aider/agent/` — tool-calling agent loop and tool registry.
- `aider/company/` — COO, orchestrator, departments, approvals, lifecycle, warehouse, playbook, audit, and config.
- `aider/memory/` — conversation memory, project memory, retrieval, consolidation, and patterns.
- `aider/mcp/` — MCP configuration, manager, adapters, and server helpers.
- `aider/integrations/` — Discord integration.
- `tests/company/` — focused coverage for settings, prompt caching, Company workflow, approvals, UX schemas, COO behavior, warehouse CLI, Discord lifecycle, and desktop behavior.

---

## Developer checks

Useful local checks:

```bash
python -m py_compile aider/gui.py aider/desktop.py aider/gui_settings_manager.py
python -m pytest tests/company/test_settings_helpers.py
python -m pytest tests/company
```

---

## Notes

Aider Plus is experimental software. The browser GUI, desktop GUI, Company Mode, MCP integrations, and warehouse flows are evolving quickly. Use git branches, review generated changes, and keep approval gates enabled for important work.
