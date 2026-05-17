# MCP Approval-Aware Tools

Aider Plus exposes MCP as an operations surface, not as a governance bypass. The
MCP manager records a permission level for each approval-aware tool:

- `read_only` — safe inspection of local Company state.
- `requires_approval` — any action that can modify state, trigger work, approve a
  proposal, or affect external systems. These calls must pass through
  `ApprovalManager.create_request()` or the orchestrator approval handler before
  execution.

## Built-in tools

Run `aider mcp tools` to list the current tool surface and permission levels.

Read-only tools:

- `list_skills()`
- `get_skill(name)`
- `list_pending_skill_proposals()`
- `get_recent_daemon_runs()`
- `get_knowledge_overview()`
- `search_knowledge(query)`
- `get_company_status()`
- Existing context tools such as `list_status()`, `list_context_memory()`, and
  `list_approvals()`

Approval-required tools:

- `approve_skill_proposal(id, feedback)`
- `trigger_daemon_run(issue_id)`
- Existing mutating submission/resolution tools such as `submit_headless_task()`,
  `submit_company_task()`, and `resolve_approval()`

## Design rule

No MCP tool should bypass Company governance, human approvals, or the
orchestrator. If a tool writes files, mutates memory, starts a daemon run,
approves a proposal, calls production-adjacent services, or delegates work, mark
it `requires_approval` and route it through the shared approval path.
