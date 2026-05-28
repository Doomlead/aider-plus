# New Contributor Playbook

This playbook turns the architecture overview in
[First Code Tour](../architecture/first-code-tour.md) into practical first-PR
paths. Use it after reading the README quickstart and before making broad
Company runtime changes.

## First bug-fix walkthrough

1. **Reproduce with the smallest command.** Prefer a focused `pytest` target or a
   single CLI command over the full suite. If the bug is in Company commands,
   start with `tests/company/test_zero_to_mvp_cli.py` or
   `tests/company/test_warehouse_cli.py`.
2. **Find the entry point.** Most user-visible behavior starts in one of:
   - `aider/main.py` for global CLI startup and classic Aider routing.
   - `aider/company/cli.py` for `aider company ...` and `aider warehouse ...` parsing.
   - `aider/company/runtime.py` for the bridge from parsed CLI commands to the
     Company orchestrator.
   - `aider/company/orchestrator.py` for department sequencing and lifecycle events.
3. **Patch the narrowest layer.** Keep parsing fixes in the parser, schema fixes
   in `aider/company/schemas/`, and department behavior in the owning department
   module.
4. **Add or adjust a regression test.** Put tests under the top-level `tests/`
   tree, matching the existing feature area (`tests/company/`, `tests/memory/`,
   `tests/mcp/`, `tests/integrations/`, or a focused root `tests/test_*.py`).
5. **Run the focused tests first, then broaden only as needed.** For example,
   run a single failing test, then the related file, then the related directory.

## How to add a Company department capability

1. **Identify the owning department.** Department implementations live in
   `aider/company/departments/`.
2. **Check the contracts.** Department outputs often flow through schemas in
   `aider/company/schemas/`, lifecycle events in `aider/company/events.py`, and
   orchestration steps in `aider/company/orchestrator.py`.
3. **Preserve handoff shape.** If a new field is needed, update the schema,
   formatting helpers, and tests that assert the handoff payload.
4. **Expose approval or risk changes explicitly.** Risky release, security,
   deployment, or tool-use changes should route through existing approval gate
   patterns instead of silently running side effects.
5. **Test at two levels when possible.** Add a unit-style department test such as
   `tests/company/test_delivery_department.py`, then add or update an
   orchestrator/runtime flow test if the capability changes sequencing.

## How to add a new `aider company ...` command

1. **Add parsing and help text in `aider/company/cli.py`.** Update the `USAGE`
   block, `CompanyCLICommand`, and `parse_company_cli()` behavior together.
2. **Route execution from the same module or the runtime bridge.** Existing
   command handlers in `aider/company/cli.py` are a good model for commands that
   inspect or mutate Company state. Commands that run multi-department work
   should continue through `aider/company/runtime.py` and
   `aider/company/orchestrator.py`.
3. **Keep `-- AIDER_ARGS...` behavior intact.** New commands that launch normal
   Aider work should preserve the passthrough convention used by `create` and
   `new`.
4. **Add tests for parsing and behavior.** Start with
   `tests/company/test_zero_to_mvp_cli.py` for `company create/new`-style flows
   or `tests/company/test_warehouse_cli.py` for warehouse-backed commands.
5. **Update README links only if the command is part of the happy path.** Put
   deep command details in the relevant doc under `docs/company/`.

## How to add tests for a department/orchestrator flow

1. **Choose the smallest seam.** Department-only behavior belongs in a focused
   `tests/company/test_<department>_department.py` test. Cross-department
   sequencing belongs in `tests/company/test_e2e_pipeline.py`,
   `tests/company/test_delivery_orchestrator.py`, or another orchestrator-focused
   file.
2. **Use existing fixtures.** Start with `tests/company/conftest.py` before
   creating new fixtures.
3. **Assert contracts, not transcripts.** Prefer structured fields, lifecycle
   event types, approval states, proof-of-work fields, and schema objects over
   brittle full prompt text.
4. **Cover failure paths for gates.** For release, security, MCP, or external
   adapter work, include denied approval, missing configuration, and retry/error
   evidence where practical.
5. **Run the narrow command.** Example:

   ```bash
   pytest tests/company/test_release_deployment.py
   ```

## Command-to-module-and-test map

| User command or surface | Primary module | Supporting modules | Tests to inspect |
| --- | --- | --- | --- |
| `aider` classic pair-programming | `aider/main.py` | `aider/coders/`, `aider/repo.py`, `aider/run_cmd.py` | `tests/basic/`, root `tests/test_*.py` |
| `aider --headless` / `--bot-mode` | `aider/main.py` | `aider/coders/`, adapter callers | root CLI/coder tests |
| `aider company init` | `aider/company/cli.py` | `aider/company/onboarding.py`, settings helpers | `tests/company/test_onboarding.py`, `tests/company/test_settings_helpers.py` |
| `aider company create` | `aider/company/cli.py` | `aider/company/runtime.py`, `aider/company/orchestrator.py`, `aider/company/templates.py` | `tests/company/test_zero_to_mvp_cli.py`, `tests/company/test_e2e_pipeline.py` |
| `aider company new` | `aider/company/cli.py` | `aider/company/warehouse.py`, `aider/company/templates.py` | `tests/company/test_warehouse_cli.py`, `tests/company/test_product_templates.py` |
| `aider company daemon` | `aider/company/daemon/` | `aider/company/workflow.py`, `aider/company/tracker.py` | `tests/company/test_symphony_daemon.py`, `tests/company/test_tracker_adapters.py` |
| `aider company memory ...` | `aider/company/cli.py` | `aider/memory/`, `aider/company/skills.py` | `tests/memory/`, `tests/company/test_memory_*.py` |
| Browser GUI | `aider/gui.py` | `aider/company/events.py`, `aider/company/surface_messages.py` | GUI-adjacent Company tests, desktop API tests |
| Desktop GUI | `aider/desktop.py` | `aider/desktop_api.py`, workspace helpers | `tests/test_desktop_*.py`, `tests/test_workspace.py` |
| Discord adapter | `aider/integrations/discord.py` | `aider/company/events.py`, runtime bridge | `tests/company/test_discord_lifecycle.py`, `tests/integrations/test_thin_adapters.py` |
| MCP tooling | `aider/mcp/` | Company runtime/tool permissions | `tests/mcp/test_mcp_integration.py`, `tests/company/test_tool_permissions.py` |

## First PR checklist

- Keep the README quickstart-focused; add detailed behavior to a doc under
  `docs/` and link it from the README only when it helps orientation.
- Add a focused regression test under the top-level `tests/` tree.
- Run the smallest useful pytest target and any syntax/import checks relevant to
  the touched modules.
- Update `CONTRIBUTING.md` if you discover process guidance that no longer
  matches the repository.
