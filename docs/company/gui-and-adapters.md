# GUI and Chat Adapters

Aider Plus exposes the same repo-native and Company runtime through multiple
surfaces. The browser GUI, desktop GUI, Discord adapter, and headless/bot mode
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

## Discord adapter

- Primary module: `aider/integrations/discord.py`.
- Discord lifecycle behavior is covered by
  `tests/company/test_discord_lifecycle.py` and thin adapter tests in
  `tests/integrations/test_thin_adapters.py`.

## Event and status contract

GUI and adapter code should listen to shared Company lifecycle/status/audit data
instead of scraping department output. Important shared modules include:

- `aider/company/events.py` for lifecycle event contracts;
- `aider/company/surface_messages.py` for consistent status and approval text;
- `aider/company/daemon/` for daemon run status and proof artifact metadata;
- `aider/company/coo.py` for CEO-facing status questions and routing.

## Focused tests

```bash
python -m pytest tests/test_desktop_api.py tests/test_desktop_task_sessions.py tests/test_desktop_workspace_startup.py
python -m pytest tests/company/test_discord_lifecycle.py tests/integrations/test_thin_adapters.py
```
