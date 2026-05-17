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
  # For GitHub Issues, use kind: github and add a github section:
  # repo: owner/repo
  # github:
  #   cache_ttl_seconds: 300
  #   max_retries: 2
  #   labels:
  #     todo: company:todo
  #     in_progress: company:in-progress
  #     retry: company:retry
  #     done: company:done
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
repository issue tracker. Configure a personal access token with environment
variables:

```bash
export GITHUB_TOKEN=ghp_your_token
export GITHUB_REPO=owner/repo
```

For a more secure production setup, authenticate with a GitHub App installation
instead of a personal access token. Install the optional GitHub extra first when
you need GitHub App JWT signing:

```bash
python -m pip install -e '.[github]'
export GITHUB_APP_ID=12345
export GITHUB_APP_INSTALLATION_ID=67890
export GITHUB_APP_PRIVATE_KEY_PATH=/secure/path/aider-company.private-key.pem
export GITHUB_REPO=owner/repo
```

Then set the workflow tracker to GitHub:

```yaml
tracker:
  kind: github
  repo: owner/repo  # optional when GITHUB_REPO is set
  labels: [aider-plus]
  github:
    cache_ttl_seconds: 300  # 5-minute issue-list cache for frequent ticks
    max_retries: 2          # retry rate-limited/transient API responses
    retry_backoff_seconds: 1.0
    labels:
      todo: company:todo
      in_progress: company:in-progress
      retry: company:retry
      done: company:done
```

Or override the tracker from the CLI for a workflow that already defines the
labels, workspace, agent, company, and hooks policy:

```bash
aider company daemon --workflow AIDER_WORKFLOW.md --tracker github --repo owner/repo --once
```

GitHub issue state is mapped into daemon states with labels: open issues are
`todo` by default, `in_progress`/`running`/`claimed`/`needs_review` labels map to
`in_progress`, `retry`/`blocked`/`failed` map to `retry`, and closed issues or a
`done` label map to `done`. The optional `tracker.github.labels` mapping lets
teams use their own label names while keeping the daemon states stable. Claiming
an issue applies `in_progress`; completing a run applies `done` and closes the
issue. Comments and PR links are posted back to the issue; PR attachment comments
include a ProofOfWork Markdown link, an executive summary, completed/failed stage
counts, and the human-review flag. The adapter caches issue-list results for 5
minutes by default (configurable up to 10 minutes) and retries rate-limited or
transient GitHub responses with exponential backoff before surfacing an error.
Retry counters, the most recent API error, and recent retry events are exposed in
daemon status.

Linear can also be used as a real tracker adapter when `LINEAR_API_KEY` is set:

```yaml
tracker:
  kind: linear
  labels: [aider-plus]
  linear:
    max_retries: 2
    retry_backoff_seconds: 1.0
    states:
      in_progress: started
      done: completed
      retry: unstarted
```

## CLI

Run one daemon tick without executing a runner:

```bash
aider company daemon --workflow AIDER_WORKFLOW.md --once --dry-run
```

Show status for existing run workspaces, including retry totals, the latest error,
tracker retry stats, and the most recent proof link:

```bash
aider company daemon --workflow AIDER_WORKFLOW.md --status
```

Stream the shared Company EventBus while a daemon run executes:

```bash
aider company daemon --workflow AIDER_WORKFLOW.md --once --watch
```

A dry run creates the issue workspace, initializes Git, renders the Company Mode
prompt, writes run state, and writes proof-of-work JSON. Production callers can
attach a runner to `CompanyDaemon` to execute Aider headlessly and return changed
files, check results, QA/review status, PR URL, and risk notes. The built-in
runner publishes `daemon_run_progress` events to the shared typed EventBus. Each
progress payload includes completed/failed counts plus retry count and last-error
fields, so CLI `--watch`, browser/desktop timelines, Discord forwarding, and
future API/MCP streams see the same observable progress envelope.

## Safety model

Hooks run as trusted shell snippets inside the issue workspace and must have a
positive timeout. Keep hooks repository-owned, review them like code, and avoid
placing secrets in hook output. External tracker adapters should preserve the
same approval posture as the existing COO/MCP model: writing comments is low
risk, but state transitions, PR attachment, deployment, and destructive tools
should be allowlisted and approval-gated.
