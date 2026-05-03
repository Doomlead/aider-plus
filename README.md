<p align="center">
  <a href="https://aider.chat/"><img src="https://aider.chat/assets/logo.svg" alt="Aider Logo" width="280"></a>
</p>

<h1 align="center">Aider Plus</h1>

<p align="center">
<strong>Aider Plus</strong> is a fork of <a href="https://github.com/Aider-AI/aider">aider-chat</a> that keeps Aider’s proven code-editing core while adding an <strong>agent runtime</strong>, <strong>headless automation flows</strong>, and <strong>team-oriented integrations</strong> (starting with Discord).
</p>

---

## Table of contents

- [What Aider Plus does](#what-aider-plus-does)
- [Core capabilities](#core-capabilities)
- [Architecture overview](#architecture-overview)
- [Quickstart](#quickstart)
- [Installation](#installation)
- [Common workflows](#common-workflows)
- [Configuration and model providers](#configuration-and-model-providers)
- [Discord integration](#discord-integration)
- [Memory and project intelligence](#memory-and-project-intelligence)
- [Benchmarking and evaluation](#benchmarking-and-evaluation)
- [Docs and website](#docs-and-website)
- [Development](#development)
- [Safety model](#safety-model)
- [Roadmap direction](#roadmap-direction)

---

## What Aider Plus does

Aider Plus is designed for **agentic software delivery loops**:

1. Understand repository context, state, and instructions.
2. Decide when to answer directly vs. implement code changes.
3. Execute edits through Aider’s existing coder pipeline.
4. Iterate through bounded multi-step loops.
5. Emit structured results/events suitable for bots, automation, and observability.

In short: this repo is both a powerful interactive coding assistant **and** an embeddable runtime for software agents.

---

## Core capabilities

### 1) Agent loop runtime
- Dedicated `aider/agent` module with loop + tool abstractions.
- Multi-step planning/execution with bounded iterations.
- Structured outcomes for downstream consumers (bots, orchestrators, services).

### 2) Proven Aider editing engine
- Git-aware code modification workflows.
- Multiple edit strategies/coders (`editblock`, `wholefile`, `udiff`, etc.).
- Strong repo-context handling with map generation and prompt shaping.

### 3) Headless automation path
- Designed for non-interactive usage (`--headless`, always-yes style execution).
- Suitable for CI assistants, background workers, and service wrappers.

### 4) Team/channel integrations
- Discord integration module (`aider/integrations/discord.py`).
- Session-aware conversational operation with streaming updates.

### 5) Broad model/provider support
- Built on LiteLLM-compatible provider model routing.
- OpenAI and many non-OpenAI providers via config/env-based setup.
- Model metadata/settings shipped in `aider/resources/`.

### 6) Optional multimodal & voice affordances
- Voice module and watch/copy-paste helpers.
- URL/page scraping utilities and browser-related support paths.

### 7) Memory primitives
- Conversation/project memory modules under `aider/memory/`.
- Foundations for longer-lived project-aware behavior.

---

## Architecture overview

High-level flow:

- **CLI entrypoint**: `aider/main.py`, `aider/__main__.py`
- **Command/runtime layer**: `aider/commands.py`, `aider/io.py`, `aider/repo.py`
- **Coder engine**: `aider/coders/*` (ask, editblock, wholefile, udiff, architect, etc.)
- **Agent layer**: `aider/agent/loop.py`, `aider/agent/tools.py`
- **Integrations**: `aider/integrations/discord.py`
- **Support systems**: memory, analytics, model management, scraping, watch, lint/test hooks

This keeps backward-compatible Aider-style coding while enabling richer external orchestration.

---

## Quickstart

### Minimal interactive usage

```bash
python -m pip install aider-chat
export OPENAI_API_KEY=your_key_here
cd /path/to/your/repo
aider
```

### Headless/automation-style usage

```bash
export OPENAI_API_KEY=your_key_here
cd /path/to/your/repo
aider --headless --yes-always --model o3-mini .
```

---

## Installation

### Standard local install

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### Development install

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
pip install -r requirements/requirements-dev.txt
```

### Optional extras

- Playwright/browser support requirements are listed in `requirements/requirements-browser*.txt`.
- Discord bot implementations generally need `discord.py`.

---

## Common workflows

### Interactive coding assistant
- Start `aider` in your repo.
- Ask for changes, refactors, tests, docs, and multi-file edits.
- Review generated diffs/commits in your normal git flow.

### Headless coding worker
- Run with headless + auto-approve flags.
- Provide task prompts from an external queue/system.
- Consume structured outputs for logs/UI/reporting.

### Channel bot / team assistant
- Route messages into the agent loop.
- Stream lifecycle events to users while work runs.
- Persist per-session context and enforce repo policies.

---

## Configuration and model providers

Aider Plus supports the same general model configuration style as Aider:

- API keys via environment variables.
- CLI flags and config files.
- Provider-specific routing through LiteLLM-style model naming.

See:
- `aider/resources/model-metadata.json`
- `aider/resources/model-settings.yml`
- Website docs under `aider/website/docs/llms/` and `aider/website/docs/config/`

---

## Discord integration

The Discord integration (`aider.integrations.discord`) is the first major channel adapter in this fork.

What it enables:
- Message-to-agent-loop routing.
- Session-aware interaction patterns.
- Timeout and policy guardrails.
- Progress/lifecycle event streaming back to Discord users.

Install dependency as needed:

```bash
pip install discord.py
```

---

## Memory and project intelligence

The repository includes memory components for:

- Conversation state tracking.
- Project memory and retrieval primitives.
- Dream/summary style background memory modules.

These are intended to support longer-horizon agent behavior over time.

---

## Benchmarking and evaluation

`benchmark/` contains scripts and datasets used for model/editing evaluation and leaderboard generation, including SWE-bench-related workflows, plotting, and dockerized runs.

Use this area to:
- Compare models/providers.
- Measure edit quality and success rates.
- Track performance over time.

---

## Docs and website

This repo includes a full docs website source under `aider/website/` with:

- install/usage/config/LLM docs,
- benchmark and leaderboard pages,
- troubleshooting and examples,
- blog posts and release-related content.

If you need end-user docs updates, start from `aider/website/docs/`.

---

## Development

### Run tests

```bash
pytest -q
```

### Run targeted tests

```bash
pytest -q tests/basic/test_discord_integration.py
pytest -q tests/basic/test_main.py
```

### Helpful repo locations

- Core package: `aider/`
- Tests: `tests/`
- Benchmarks: `benchmark/`
- Website/docs: `aider/website/`
- Utility scripts: `scripts/`

---

## Safety model

Aider Plus follows a practical safety posture for code agents:

- Bounded iteration loops.
- Repository-aware execution.
- Explicit tool path for implementation.
- Headless operation intended for controlled environments.
- Human-reviewable git-native outputs.

---

## Roadmap direction

Near-term evolution focuses on:

- stronger loop/planning reliability,
- richer structured outputs for integrations,
- additional channel adapters beyond Discord,
- better long-horizon memory use,
- tighter observability and evaluation loops.

---

## Upstream Aider references

Because this is a fork, upstream docs remain highly relevant:

- https://aider.chat/docs/install.html
- https://aider.chat/docs/usage.html
- https://aider.chat/docs/llms.html


## Aider Plus commit additions summary

Below is a concise summary of what each Aider Plus commit introduced (based on commit messages):

- `46f43ae` — Removed an obsolete `prepare_messages_for_llm` stub.
- `d121a2d` — Refactored the agent loop to use coder-managed message formatting.
- `bd3c1a2` — Switched agent loop message preparation to coder message APIs when available.
- `d34e119` — Simplified loop message flow by routing through `coder.run`.
- `8883c2c` — Improved user-message handoff inside the agent loop.
- `af4656f` — Added architect/editor orchestration flow to the agent loop.
- `44a901d` — Added a minimal `ToolRegistry` for agent tool execution.
- `5979780` — Fixed malformed HTTP scraper `User-Agent` header handling.
- `4154d4b` — Added dream-consolidation support for Discord session memory.
- `edda09f` — Added initial company-orchestration code sketches.
- `5f80841` — Refactored Discord execution flow to use `EngineeringDepartment` tasks.
- `5c14ef7` — Wired Discord bot flow through `CompanyOrchestrator` scaffolding.
- `98cc4a0` — Added guided onboarding flow and first-run prompts.
- `01b0222` — Added onboarding prompt support for Discord bot token setup.
- `3b19a1e` — Rewrote README with a full Aider Plus capabilities overview.
- `f03bb9e` — Updated README details to reflect current project state.

(Associated merge commits primarily integrate these feature commits and branch changes.)
