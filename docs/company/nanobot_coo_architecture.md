# Nanobot-inspired COO architecture

Phase 1.5 adds a local, Nanobot-inspired communication layer without importing or
bridging to Nanobot at runtime. The intent is to keep Aider Plus company mode as
the execution engine while giving every user channel a persistent COO entry point
and every department its own agent loop.

```text
User (CLI / Discord / Desktop)
        |
        v
NanobotCOO (message bus + durable session manager + COO agent loop)
        |
        v
CompanyOrchestrator
        |
        v
Departments, each with a dedicated AiderAgentLoop
  - ProductAgent
  - UXAgent
  - EngineeringAgent (programmer + reviewer config)
  - QAAgent
  - DevOpsAgent
```

## Responsibilities

- `NanobotCOO` owns channel-facing communication, durable per-channel sessions,
  and optional LLM-based routing.
- `CompanyOrchestrator` remains the canonical workflow, approval, lifecycle,
  context-building, and audit coordinator.
- Departments remain the execution units, but the app entry points now construct
  one `AiderAgentLoop` per company agent role instead of sharing one loop across
  Product, UX, and Engineering.

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

## Routing mode

By default, the COO routes deterministically to avoid adding an extra LLM call to
every user turn. Set `CompanyConfig.enable_coo_llm_routing=True` to allow the COO
agent loop to classify the target department from session history and the prompt.
