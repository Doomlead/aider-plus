# GUI and Chat Adapters

Aider Plus exposes the same repo-native and Company runtime through multiple
surfaces. The browser GUI, desktop GUI, Discord, Slack-compatible, Matrix, and headless/bot mode
should reuse shared runtime contracts instead of creating separate workflow
semantics.

## Browser GUI

- Primary module: `aider/gui.py`.
- Intended use: local browser control surface for Company setup, status,
  approvals, memory/status inspection, and task launching.
- Shared dependencies: Company events, surface messages, daemon status, and
  security/release summaries.

## Desktop GUI

- Primary module: `aider/desktop.py`.
- Supporting workspace/API paths include desktop API and workspace helpers used
  by tests under `tests/test_desktop_*.py` and `tests/test_workspace.py`.
- Intended use: native local control surface with task sessions, workspace
  startup behavior, and Company status panels.

## Chat targets and headless mode

- `aider --headless --msg "..."` runs a single non-interactive task.
- `--bot-mode` is an alias for `--headless`.
- Adapters should preserve the same Aider args passthrough and Company command
  semantics that CLI users get directly.

## Chat adapters

- Discord primary module: `aider/integrations/discord.py`.
- Slack/webhook-compatible primary module: `aider/integrations/slack.py`
  (also used by Teams and Mattermost shims).
- Matrix primary module: `aider/integrations/matrix.py`. It extracts Matrix
  room event identity (`room_id`, `event_id`, `sender`, and `content.body`)
  into `AdapterMessage`, delegates inbound text through `ThinAdapter`, and
  forwards shared `EventBus` status messages rather than owning workflow logic.
- Inbound chat work should be configured with `runtime_executor` on
  `ThinAdapter` subclasses so `handle_user_input()` builds a
  `CompanyRunRequest` and calls `run_company_task()` before any concrete
  executor or COO/orchestrator code runs. Legacy/custom `input_handler` hooks are
  only for normalization tests and compatibility shims; production surfaces must
  not route directly to departments or coder loops.
- Discord lifecycle behavior is covered by
  `tests/company/test_discord_lifecycle.py`. Slack and Matrix lifecycle parity,
  shared `surface_messages.py` rendering, and runtime-contract delegation are
  covered by `tests/integrations/test_thin_adapters.py`.

## Event and status contract

GUI and adapter code should listen to shared Company lifecycle/status/audit data
instead of scraping department output. Adapter-facing text must come from
`aider/company/surface_messages.py` (for example, `format_runtime_event_message()`
or the Discord block wrapper) so browser, desktop, Discord, Slack-compatible,
Matrix, and headless/bot surfaces keep the same labels, warnings, progress bars,
and truncation behavior. Important shared modules include:

- `aider/company/events.py` for lifecycle event contracts;
- `aider/company/surface_messages.py` for consistent status badges, progress,
  deployment, approval, and lifecycle text;
- `aider/company/daemon/` for daemon run status and proof artifact metadata;
- `aider/company/coo.py` for CEO-facing status questions and routing.

## Focused tests

```bash
python -m pytest tests/test_desktop_api.py tests/test_desktop_task_sessions.py tests/test_desktop_workspace_startup.py
python -m pytest tests/company/test_discord_lifecycle.py tests/integrations/test_thin_adapters.py
```
