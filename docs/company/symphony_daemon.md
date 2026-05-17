# Symphony-inspired Company daemon

Aider Plus can run Company Mode as a small, local orchestration daemon inspired by
OpenAI Symphony. The daemon keeps Aider Plus Python-native: it does not import the
Symphony prototype, and it uses the existing Company Mode prompt, warehouse, COO,
approval, MCP, QA, and proof-of-work patterns as the execution model.

## What the daemon provides

- **Repo-owned workflow policy** in an `AIDER_WORKFLOW.md` or
  `.aider/company/WORKFLOW.md` file.
- **Tracker adapter boundary** with local JSON and GitHub Issues adapters, plus
  room for Linear or other tracker drop-ins.
- **One isolated Git workspace per issue** under a configured runs directory.
- **Bounded daemon ticks** controlled by `agent.max_concurrent_agents` and
  `agent.max_attempts`.
- **Lifecycle hooks** for setup, smoke checks, artifact collection, and cleanup.
- **Proof-of-work JSON** for every run at
  `.aider/company/proof-of-work.json` inside the run workspace.
- **Status output** that can feed the COO/dashboard observability surface.

## Workflow file

A workflow is Markdown with optional YAML front matter:

```markdown
---
tracker:
  kind: local
  path: .aider/company/issues.json
  labels: [aider-plus]
workspace:
  root: ./AiderPlusWarehouse/runs
  clean: false
agent:
  max_concurrent_agents: 2
  max_attempts: 2
company:
  template: nextjs-app
  route: product_to_release
  require_release_approval: true
hooks:
  timeout_seconds: 120
  after_create: |
    git status --short
  before_run: |
    uv sync || true
  after_run: |
    git status --short
---
Work on {{ issue.identifier }}: {{ issue.title }}.

Route the request through Aider Plus Company Mode and produce proof of work:
summary, changed files, checks, QA result, review result, risks, and whether a
human needs to review before merge or release.
```

The prompt body supports these placeholders:

- `{{ issue.identifier }}`
- `{{ issue.title }}`
- `{{ issue.description }}`
- `{{ issue.url }}`

## Local JSON tracker

The local adapter is deliberately deterministic:

```json
{
  "version": 1,
  "issues": [
    {
      "identifier": "AP-1",
      "title": "Add billing reports",
      "description": "Create dashboard export support.",
      "status": "todo",
      "labels": ["aider-plus"]
    }
  ]
}
```

Eligible statuses are `todo`, `ready`, `open`, and `retry`. The daemon updates
status, comments, and pull request URLs in the same JSON file. This mirrors the
same adapter operations used by the GitHub adapter: list candidates, claim,
comment, transition, and attach a PR.


## GitHub Issues tracker

Use the GitHub adapter when you want the daemon to pull work from a real
repository issue tracker. Configure credentials with environment variables:

```bash
export GITHUB_TOKEN=ghp_your_token
export GITHUB_REPO=owner/repo
```

Then set the workflow tracker to GitHub:

```yaml
tracker:
  kind: github
  repo: owner/repo  # optional when GITHUB_REPO is set
  labels: [aider-plus]
```

Or override the tracker from the CLI for a workflow that already defines the
labels, workspace, agent, company, and hooks policy:

```bash
aider company daemon --workflow AIDER_WORKFLOW.md --tracker github --repo owner/repo --once
```

GitHub issue state is mapped into daemon states with labels: open issues are
`todo` by default, `in_progress`/`running`/`claimed` labels map to
`in_progress`, `retry` maps to `retry`, and closed issues or a `done` label map
to `done`. Claiming an issue applies `in_progress`; completing a run applies
`done` and closes the issue. Comments and PR links are posted back to the issue.

## CLI

Run one daemon tick without executing a runner:

```bash
aider company daemon --workflow AIDER_WORKFLOW.md --once --dry-run
```

Show status for existing run workspaces:

```bash
aider company daemon --workflow AIDER_WORKFLOW.md --status
```

A dry run creates the issue workspace, initializes Git, renders the Company Mode
prompt, writes run state, and writes proof-of-work JSON. Production callers can
attach a runner to `CompanyDaemon` to execute Aider headlessly and return changed
files, check results, QA/review status, PR URL, and risk notes.

## Safety model

Hooks run as trusted shell snippets inside the issue workspace and must have a
positive timeout. Keep hooks repository-owned, review them like code, and avoid
placing secrets in hook output. External tracker adapters should preserve the
same approval posture as the existing COO/MCP model: writing comments is low
risk, but state transitions, PR attachment, deployment, and destructive tools
should be allowlisted and approval-gated.
