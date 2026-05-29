# DevOps Release Execution

DevOps is the Company department responsible for turning a Delivery-approved
handoff into build, package, deploy, verification, rollback, and release evidence.
It is intentionally gated: high-risk or externally visible release actions should
require explicit approval rather than silently running side effects.

## Where it fits

```text
Product -> UX -> Delivery -> Engineering -> Reviewer -> QA -> Delivery -> DevOps
```

Delivery owns readiness and handoff quality. DevOps owns release execution after
that handoff is approved.

## Implementation map

- `aider/company/departments/devops.py` — DevOps department behavior.
- `aider/company/orchestrator.py` — sequencing, approvals, and lifecycle events.
- `aider/company/schemas/` — structured handoff, build artifact, deployment, and
  rollback contracts.
- `aider/company/surface_messages.py` — shared release/status/audit messaging.
- `aider/company/daemon/` — unattended issue workflow execution and proof-of-work
  persistence when daemon runs reach release stages.

## Operational expectations

DevOps changes should preserve these behaviors:

- validate Delivery handoffs before running build or deployment steps;
- detect build/package/deploy commands from supported project metadata where
  possible;
- capture command output, artifact metadata, deployment provider results, and
  rollback instructions;
- prepare a dry-run preview before provider execution, including planned
  commands, approval requirements, log capture destinations, and rollback
  recovery data;
- record release status in audit/proof-of-work surfaces;
- route high-risk actions through approval gates;
- keep partial-success and retry evidence visible to the daemon, COO, and GUI
  status surfaces.

## Supported deployment providers

DevOps can run local manifest recording plus provider commands for Vercel,
Railway, Fly.io, AWS, Docker Compose, Netlify, Render, Cloudflare Pages,
Kubernetes, and Helm when the provider is enabled in
`devops_deployment_providers`. Provider commands are treated as high-risk and
must pass the appropriate approval gates before execution; production provider
deploys are blocked before command execution when approval metadata is missing.

Every deployment result records rollback metadata alongside the legacy rollback
command/url fields. The metadata captures the provider/environment, current and
previous artifacts, owner, Delivery rollback plan presence, validation steps,
and generation timestamp so dashboards and operators have enough context to
preview or execute recovery safely.

Set `devops_dry_run` or `dry_run` on a DevOps task to build the artifact and
return a deployment preview without running provider commands. The preview shows
the commands that would run, whether approval is required/granted, where logs
would be captured/uploaded, and which rollback metadata would be attached.

## Focused tests

Run release seam tests whenever changing Delivery -> DevOps handoffs, deployment
provider commands, approval gates, artifact metadata, rollback handling, or proof
of work release fields:

```bash
python -m pytest tests/company/test_release_deployment.py tests/company/test_devops_department.py
```

Broaden to daemon tests if release behavior is visible in issue-driven runs:

```bash
python -m pytest tests/company/test_symphony_daemon.py
```
