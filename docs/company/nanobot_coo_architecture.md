# Nanobot-inspired CEO/COO architecture

Aider Plus keeps the existing Company Mode execution engine, but the COO is now
modeled as a Nanobot-inspired personal assistant framework instead of only a
routing shim. The human user is the **Chief Executive Officer (CEO)**. The local
`NanobotCOO` is the **Chief Operating Officer (COO)**: a persistent assistant
that can answer the CEO directly, remember CEO preferences, inspect operating
status, ask clarifying questions, use approved tools, and delegate work to the
internal Aider Plus company.

This remains a local implementation: Aider Plus does not import or bridge to
Nanobot at runtime. It borrows the lightweight pattern of a persistent session,
small action loop, memory, tools, and channel adapters while preserving Aider
Plus orchestration as the execution backend.

```text
CEO (CLI / Discord / Browser / Desktop)
        |
        v
NanobotCOO personal assistant loop
  - durable CEO/COO session
  - COOActionDecision
  - COO profile + repo-local memory
  - status and approval briefing
  - optional tool/MCP adapter points
        |
        +--> answer CEO directly / ask clarification / remember / recall / inspect
        |
        v
Company bridge: delegate_company_task
        |
        v
CompanyOrchestrator
        |
        v
Internal departments, each with a dedicated AiderAgentLoop
  - ProductAgent
  - UXAgent
  - EngineeringAgent (programmer + reviewer config)
  - QAAgent
  - DevOpsAgent
```

## Mental model

- **CEO**: the human user and final authority.
- **COO**: the persistent Nanobot-style personal assistant and company operator.
- **Departments**: internal execution teams owned by `CompanyOrchestrator`.
- **CompanyOrchestrator**: the canonical workflow, approval, lifecycle,
  context-building, handoff, and audit coordinator.

The COO should not replace Product → UX → Engineering → QA → DevOps. The COO
chooses whether the CEO needs a direct response, clarification, memory update,
status briefing, tool use, or delegation. When delegation is needed, it calls
into the existing company workflow instead of duplicating it.

## COO action loop

`COOActionDecision` is the CEO-facing decision object. It allows the COO to
select one of these actions before any department routing happens:

- `answer_directly`: respond to the CEO without creating a department task.
- `ask_ceo_clarification`: ask the CEO for missing information or approval.
- `inspect_status`: produce an operating brief from company/session state.
- `update_memory`: persist a CEO preference or operational note.
- `recall_memory`: summarize remembered CEO preferences or notes.
- `use_tool`: reserved for approved COO tools/MCP adapters.
- `delegate_company_task`: bridge into `CompanyOrchestrator`.

`COORouteDecision` remains the lower-level department-routing object used when
`COOActionDecision.action == "delegate_company_task"`. This preserves backward
compatibility for existing route histories, deterministic routing, LLM routing,
retry/fallback behavior, and human escalation metadata.

## COO memory

The first implementation keeps personal COO memory repo-local:

```text
.aider/coo/profile.json
.aider/coo/memory.jsonl
```

`profile.json` stores the CEO/COO operating profile and default approval style.
`memory.jsonl` stores durable CEO preferences and notes captured by the COO. A
future global layer can add `~/.aider-plus/coo/profile.json`,
`~/.aider-plus/coo/memory.jsonl`, and `~/.aider-plus/coo/tasks.jsonl` without
changing the department orchestration contract.

## Per-agent model selection

Users can assign models per agent with environment overrides before launching a
company session:

```bash
AIDER_COMPANY_AGENT_MODELS="coo=gpt-4o,product=claude-sonnet-4-5,engineering=o3"
AIDER_COMPANY_MODEL_QA="gpt-4o-mini"
AIDER_COMPANY_MODEL_DEVOPS="claude-sonnet-4-5"
```

The supported agent names are `coo`, `product`, `ux`, `engineering`, `reviewer`,
`qa`, and `devops`. Programmatic callers can also set `DepartmentConfig` entries
on `CompanyConfig` directly.

## Personal action and routing modes

By default, the COO handles simple personal-assistant intents locally: CEO
briefings/status, memory updates, and memory recall do not create department
tasks. Other work falls through to deterministic department routing so the COO
remains useful without adding an LLM call to every turn.

Set `CompanyConfig.enable_coo_llm_routing=True` to allow the COO agent loop to
choose a richer `COOActionDecision` from CEO history, COO memory, current company
status, and the prompt. If the action delegates to the internal company, the COO
uses `COORouteDecision` and the existing retry/fallback/human-escalation path.

## Initial COO tools

The current local capabilities establish the tool contract without copying
Nanobot internals:

- `delegate_company_task`: submit work to Product/UX/Engineering/QA/DevOps.
- `inspect_company_status`: build CEO briefings from orchestrator/session state.
- `get_pending_approvals`: included in status payloads as pending CEO approvals.
- `answer_ceo`: direct COO response.
- `ask_ceo_clarification`: clarification before risky/ambiguous execution.
- `remember_ceo_preference`: repo-local COO memory append.
- `recall_ceo_memory`: repo-local COO memory retrieval.

Later adapters can add `schedule_followup`, `summarize_project`, `draft_update`,
`run_aider_task`, and `use_mcp_tool`. Existing Aider Plus MCP discovery can still
convert MCP tools into `ToolRegistry` tools with allowlists and approval gates.

## Observability and session status APIs

The COO message bus is the shared observability spine for CLI, Desktop, Browser,
and Discord surfaces. `COOMessageBus.get_formatted_events(limit=20)` returns
recent human-readable event strings that include timestamps, session keys, queue
state, action/delegation metadata, routing decisions, task IDs, and department
handoffs. Raw events remain available through
`COOMessageBus.snapshot()["recent_events"]`, while
`snapshot()["formatted_events"]` gives UIs the same centralized formatting.

Each `COOSession` exposes `snapshot()`, a compact persistence view with recent
messages, route history, the active department, and the last deliverable summary.
`NanobotCOO.get_session_status(session_id)` combines that persisted session
snapshot with bus metrics, `last_coo_action`, `ceo_profile`, recent COO memory,
route history, errors, pending escalations, and queue counters into a dashboard
payload.

Desktop's Company Dashboard includes a COO Activity section that refreshes from
`get_session_status()`. Discord exposes the same payload via the `coo_status`
command, with `session` as an alias. Bus event handlers also forward live COO
activity into the existing Desktop event queue and Discord lifecycle stream when
a Discord company event callback is registered.
