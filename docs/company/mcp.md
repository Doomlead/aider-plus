# MCP Approval-Aware Tools

Aider Plus exposes MCP as an operations surface, not as a governance bypass. The
MCP manager records a permission level and a human-readable approval reason for
each approval-aware tool:

- `read_only` — safe inspection of local Company state. These tools explain that
  no shared approval gate is required because they only inspect state.
- `requires_approval` — any action that can modify state, trigger work, approve a
  proposal, or affect external systems. These calls must pass through
  `ApprovalManager.create_request()` or the orchestrator approval handler before
  execution, and their approval request includes the reason the tool is gated.

## Inspecting approval UX

Run `aider mcp tools` to list the current tool surface, permission levels, and
why each tool does or does not require approval. The COO/GUI-facing
`list_available_mcp_tools` and `explain_mcp_tool` actions return the same
`approval_reason` field so chat and graphical surfaces can show the policy
without duplicating logic.

Denied MCP operations now return or raise audit-ready messages in the form
`Denied MCP operation <tool-or-task>: <reason>`. When a denial comes from the
shared approval manager, the human-provided reason is preserved so logs and UIs
can explain what blocked the operation.

## Built-in tools

Read-only tools:

- `list_skills()` — approved skill inventory; no approval required.
- `get_skill(name)` — read one approved skill; no approval required.
- `list_pending_skill_proposals()` — inspect pending proposals; no approval
  required.
- `get_recent_daemon_runs()` — inspect daemon history; no approval required.
- `get_knowledge_overview()` — summarize institutional knowledge; no approval
  required.
- `search_knowledge(query)` — search local institutional knowledge; no approval
  required.
- `get_company_status()` — inspect status; no approval required.
- Code graph inspection tools such as `codegraph_status()`,
  `codegraph_search()`, `codegraph_context()`, `codegraph_callers()`,
  `codegraph_callees()`, `codegraph_impact()`, and `codegraph_affected()`.
- Existing context tools such as `list_status()`, `list_context_memory()`, and
  `list_approvals()`.

Approval-required tools:

- `approve_skill_proposal(id, feedback)` — approves institutional knowledge
  changes.
- `trigger_daemon_run(issue_id)` — starts autonomous issue work that may write
  code, comments, or status updates.
- Existing mutating submission/resolution tools such as `submit_headless_task()`,
  `submit_company_task()`, and `resolve_approval()`.

## Design rule

No MCP tool should bypass Company governance, human approvals, or the
orchestrator. If a tool writes files, mutates memory, starts a daemon run,
approves a proposal, calls production-adjacent services, or delegates work, mark
it `requires_approval` and route it through the shared approval path. Include a
clear `approval_reason` so CLI, GUI, chat, and audit logs can explain why the
operation was gated or denied.
