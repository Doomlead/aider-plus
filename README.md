<p align="center">
    <a href="https://aider.chat/"><img src="https://aider.chat/assets/logo.svg" alt="Aider Logo" width="280"></a>
</p>

<h1 align="center">Aider Plus</h1>

<p align="center">
Aider Plus is a fork of Aider focused on <strong>agentic software delivery loops</strong>: chat-first orchestration, headless coding, and channel integrations (starting with Discord).
</p>

---

## What this fork is becoming

Aider Plus is evolving from a terminal-only assistant into a reusable **software development agent runtime** with:

- an explicit agent loop (`aider/agent`) for multi-step planning and execution,
- a headless Coder tool path for safe, structured code changes,
- streaming lifecycle events for integrations,
- session-aware bot integrations for team channels.

The goal is to support “dev company style” interactions where the agent can:

1. understand repo state and project instructions,
2. decide whether to answer directly or implement,
3. apply changes through the existing Coder engine,
4. iterate up to a bounded number of steps,
5. report results in a structured way.

---

## Features

### 1) Agent loop orchestration (new)
- New top-level `aider/agent` module.
- Thin loop abstraction for 1–3 iteration execution.
- Built-in support for direct response vs tool-based code implementation.

### 2) Headless Coder as primary tool
- Uses existing `run_structured_async(...)` path for implementation steps.
- Returns structured `CoderResult` output (summary, files changed, optional diff/commit metadata).
- Keeps edits grounded in your actual repository.

### 3) Rich repo-aware context
- Includes repository state (tracked files and git status),
- recent Coder conversation history,
- and project-level system instructions in the agent prompt.

### 4) Event streaming for UX integrations
- Emits lifecycle events like:
  - `thinking`
  - `applying_edits`
  - `response_complete`
- Enables Discord (and future channels) to stream progress in real time.

### 5) Discord-first integration path
- Discord requests now pass through the new agent loop,
- while preserving session management, timeout limits, repo policy checks,
- and allow/deny safety controls.

### 6) Still Aider at the core
- Git-native workflow.
- Broad LLM compatibility through LiteLLM providers.
- Mature file editing pipeline and repo map foundation.

---

## Getting Started (Aider Plus workflow)

### 1. Install

```bash
python -m pip install aider-chat
```

### 2. Set an API key

```bash
export OPENAI_API_KEY=your_key_here
# or provider-specific env vars (ANTHROPIC_API_KEY, etc.)
```

### 3. Open your repository

```bash
cd /path/to/your/repo
```

### 4. Run headless agent-ready mode

```bash
aider --headless --yes-always --model o3-mini .
```

### 5. Integrate with Discord
Use `aider.integrations.discord.DiscordAiderBot` to process messages through the new `AiderAgentLoop` and stream lifecycle callbacks.

---

## Installation Guide

> Replaces the old “More Information” section with concrete install paths for this fork’s direction.

### Option A — Standard Python install

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### Option B — Dev install with test dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
pip install -r requirements/requirements-dev.txt
```

### Option C — Verify quickly

```bash
aider --help
pytest -q tests/basic/test_discord_integration.py
```

### Discord dependency (optional)

If you are building a Discord bot front-end, install `discord.py` in your environment:

```bash
pip install discord.py
```

---

## Roadmap (near-term)

- Harden agent loop prompts + tool-call reliability.
- Expand structured result contracts for integrations.
- Add richer channel adapters beyond Discord.
- Keep iterations bounded and safe before introducing broader multi-tool support.

---

## Upstream Aider docs

This fork builds on Aider. For general model/provider usage and core command docs:

- https://aider.chat/docs/install.html
- https://aider.chat/docs/usage.html
- https://aider.chat/docs/llms.html

