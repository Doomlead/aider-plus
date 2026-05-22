"""CLI helpers for the Aider Plus Company golden path."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from aider.company.daemon import CompanyDaemon, CompanyDaemonError, load_daemon
from aider.company.self_improvement import SelfImprovementService
from aider.company.state import CompanyStateManager
from aider.company.workflow import TrackerWorkflowConfig, WorkflowError
from aider.company.runtime import CompanyRunRequest, run_company_task
from aider.company.template_selector import select_template
from aider.memory import ProjectMemory
from aider.memory.records import MemoryRecord
from aider.memory.reinforcement import record_memory_outcome, record_skill_outcome
from aider.memory.store import MemoryStore
from aider.company.templates import (
    DEFAULT_TEMPLATE_KEY,
    get_template,
    list_templates,
    render_zero_to_mvp_prompt,
    template_alias_note,
)
from aider.company.warehouse import (
    WarehouseError,
    WarehouseManager,
    default_warehouse_path,
    slugify_product_name,
)


@dataclass(frozen=True)
class CompanyCLICommand:
    """Parsed `aider company ...` command."""

    action: str
    idea: str = ""
    template: str = DEFAULT_TEMPLATE_KEY
    template_alias_note: str | None = None
    template_selection_note: str | None = None
    template_selection_reasons: tuple[str, ...] = ()
    template_selection_memory_ids: tuple[str, ...] = ()
    project_name: str | None = None
    dry_plan: bool = False
    warehouse_path: str | None = None
    product_path: str | None = None
    workflow_path: str | None = None
    once: bool = False
    status: bool = False
    run_issue_id: str | None = None
    runner_departments: tuple[str, ...] = ()
    runner_max_iterations: int | None = None
    tracker_type: str | None = None
    repo: str | None = None
    watch: bool = False
    event_filter: str | None = None
    github_token: str | None = None
    model: str | None = None
    mcp_enabled: bool | None = None
    yes: bool = False
    first_product_idea: str | None = None
    first_product_name: str | None = None


class CompanyCLIError(ValueError):
    """Raised for invalid `aider company` invocations."""


USAGE = """Usage:
  aider company init [--warehouse PATH] [--template TEMPLATE] [--github-repo OWNER/REPO] [--github-token TOKEN] [--model MODEL] [--enable-mcp|--skip-mcp] [--product-idea IDEA] [--product-name NAME] [--yes]
  aider company setup [same options as init]
  aider company templates
  aider company create <idea> [--template TEMPLATE] [--name PROJECT_NAME] [--dry-plan] [-- AIDER_ARGS...]
  aider company new <idea> [--template TEMPLATE] [--name PRODUCT_NAME] [--warehouse PATH] [--dry-plan] [-- AIDER_ARGS...]
  aider company daemon --workflow PATH [--tracker TYPE] [--repo OWNER/REPO] [--once] [--dry-run] [--status] [--run ISSUE_ID] [--departments LIST] [--max-iterations N] [--watch] [--filter EVENT_TYPE]
  aider company memory status
  aider company memory repair [--yes]
  aider company memory backfill
  aider warehouse init [PATH]
  aider warehouse list [--warehouse PATH]
  aider warehouse open PRODUCT [--warehouse PATH]
  aider warehouse status [--warehouse PATH]

Examples:
  aider company init --warehouse ./AiderPlusWarehouse --template nextjs-saas --product-idea "Build my MVP" --product-name my-mvp --yes
  aider company templates
  aider company create "Build a habit tracker web app with login, dashboard, and streaks"
  aider company new "Build a habit tracker" --name habit-tracker --template nextjs-saas
  aider company create "Build a Stripe webhook API" --template fastapi-api -- --model gpt-5.5
""".strip()


def parse_company_cli(
    argv: Sequence[str],
) -> tuple[CompanyCLICommand | None, list[str]]:
    """Parse and strip an `aider company ...` command from argv.

    Returns `(None, argv)` when argv does not start with `company`. For company
    commands, returns the parsed command and any Aider args after `--`.
    """

    args = list(argv)
    if not args or args[0] != "company":
        return None, args
    if len(args) == 1 or args[1] in {"-h", "--help", "help"}:
        raise CompanyCLIError(USAGE)

    action = args[1]
    rest = args[2:]
    aider_args: list[str] = []
    if "--" in rest:
        marker = rest.index("--")
        aider_args = rest[marker + 1 :]
        rest = rest[:marker]

    if action in {"init", "setup"}:
        return _parse_company_onboarding(action, rest), aider_args

    if action == "templates":
        if rest:
            raise CompanyCLIError(
                "`aider company templates` does not accept extra arguments.\n" + USAGE
            )
        return CompanyCLICommand(action="templates"), aider_args

    if action == "daemon":
        return _parse_company_daemon(rest), aider_args
    if action == "memory":
        if not rest or rest[0] not in {"status", "repair", "backfill"}:
            raise CompanyCLIError(
                "`aider company memory` supports `status`, `repair`, or `backfill`.\n"
                + USAGE
            )
        return CompanyCLICommand(action=f"memory-{rest[0]}", yes="--yes" in rest), aider_args

    if action not in {"create", "new"}:
        raise CompanyCLIError(f"Unknown company command: {action}\n{USAGE}")

    template: str | None = None
    project_name: str | None = None
    dry_plan = False
    warehouse_path: str | None = None
    idea_parts: list[str] = []
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--template":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--template requires a template key.\n" + USAGE)
            template = rest[index]
        elif token == "--name":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--name requires a project name.\n" + USAGE)
            project_name = rest[index]
        elif token == "--warehouse":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--warehouse requires a path.\n" + USAGE)
            warehouse_path = rest[index]
        elif token == "--dry-plan":
            dry_plan = True
        elif token.startswith("--"):
            raise CompanyCLIError(
                f"Unknown company {action} option: {token}. Put Aider options after `--`.\n{USAGE}"
            )
        else:
            idea_parts.append(token)
        index += 1

    idea = " ".join(idea_parts).strip()
    if not idea:
        raise CompanyCLIError(
            f"`aider company {action}` requires a product idea.\n" + USAGE
        )
    requested_template = template
    template_selection_note: str | None = None
    template_selection_reasons: tuple[str, ...] = ()
    template_selection_memory_ids: tuple[str, ...] = ()
    if requested_template is None:
        decision = select_template(
            idea=idea,
            project_name=project_name,
            role_context=action,
            memory_store=MemoryStore(ProjectMemory(str(Path.cwd()))),
        )
        template = get_template(decision.template_key).key
        template_selection_note = (
            f"Auto-selected template `{template}` "
            f"(confidence {decision.confidence:.2f}). "
            f"Reasons: {'; '.join(decision.reasons[:2])}"
        )
        template_selection_reasons = tuple(decision.reasons)
        template_selection_memory_ids = tuple(decision.memory_record_ids)
    else:
        try:
            template = get_template(requested_template).key
        except ValueError as exc:
            raise CompanyCLIError(str(exc) + "\n" + USAGE) from exc

    return (
        CompanyCLICommand(
            action=action,
            idea=idea,
            template=template,
            template_alias_note=template_alias_note(requested_template),
            template_selection_note=template_selection_note,
            template_selection_reasons=template_selection_reasons,
            template_selection_memory_ids=template_selection_memory_ids,
            project_name=project_name,
            dry_plan=dry_plan,
            warehouse_path=warehouse_path,
        ),
        aider_args,
    )


def _parse_company_onboarding(action: str, args: Sequence[str]) -> CompanyCLICommand:
    warehouse_path: str | None = None
    template = DEFAULT_TEMPLATE_KEY
    repo: str | None = None
    github_token: str | None = None
    model: str | None = None
    mcp_enabled: bool | None = None
    yes: bool = False
    first_product_idea: str | None = None
    first_product_name: str | None = None
    rest = list(args)
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--warehouse":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--warehouse requires a path.\n" + USAGE)
            warehouse_path = rest[index]
        elif token == "--template":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--template requires a template key.\n" + USAGE)
            template = rest[index]
        elif token == "--github-repo":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--github-repo requires owner/repo.\n" + USAGE)
            repo = rest[index].strip()
        elif token == "--github-token":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--github-token requires a token.\n" + USAGE)
            github_token = rest[index]
        elif token == "--model":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--model requires a model name.\n" + USAGE)
            model = rest[index]
        elif token == "--enable-mcp":
            mcp_enabled = True
        elif token == "--skip-mcp":
            mcp_enabled = False
        elif token == "--product-idea":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError(
                    "--product-idea requires a product idea.\n" + USAGE
                )
            first_product_idea = rest[index]
        elif token == "--product-name":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError(
                    "--product-name requires a product name.\n" + USAGE
                )
            first_product_name = rest[index]
        elif token == "--yes":
            yes = True
        else:
            raise CompanyCLIError(
                f"Unknown company {action} option: {token}.\n" + USAGE
            )
        index += 1
    requested_template = template
    try:
        template = get_template(requested_template).key
    except ValueError as exc:
        raise CompanyCLIError(str(exc) + "\n" + USAGE) from exc
    return CompanyCLICommand(
        action=action,
        template=template,
        template_alias_note=template_alias_note(requested_template),
        warehouse_path=warehouse_path,
        repo=repo,
        github_token=github_token,
        model=model,
        mcp_enabled=mcp_enabled,
        yes=yes,
        first_product_idea=first_product_idea,
        first_product_name=first_product_name,
    )


def _parse_company_daemon(args: Sequence[str]) -> CompanyCLICommand:
    workflow_path: str | None = None
    dry_run = False
    once = False
    status = False
    run_issue_id: str | None = None
    runner_departments: tuple[str, ...] = ()
    runner_max_iterations: int | None = None
    tracker_type: str | None = None
    repo: str | None = None
    watch: bool = False
    event_filter: str | None = None
    rest = list(args)
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--workflow":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--workflow requires a path.\n" + USAGE)
            workflow_path = rest[index]
        elif token == "--dry-run":
            dry_run = True
        elif token == "--tracker":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--tracker requires a tracker type.\n" + USAGE)
            tracker_type = rest[index].strip().lower()
        elif token == "--repo":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--repo requires an owner/repo value.\n" + USAGE)
            repo = rest[index].strip()
        elif token == "--once":
            once = True
        elif token == "--status":
            status = True
        elif token == "--watch":
            watch = True
        elif token == "--filter":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--filter requires an event type.\n" + USAGE)
            event_filter = rest[index].strip()
        elif token == "--run":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--run requires an issue id.\n" + USAGE)
            run_issue_id = rest[index]
        elif token == "--departments":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError(
                    "--departments requires a comma-separated list.\n" + USAGE
                )
            runner_departments = tuple(
                item.strip().lower() for item in rest[index].split(",") if item.strip()
            )
        elif token == "--max-iterations":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError(
                    "--max-iterations requires a positive integer.\n" + USAGE
                )
            try:
                runner_max_iterations = int(rest[index])
            except ValueError as exc:
                raise CompanyCLIError(
                    "--max-iterations must be a positive integer.\n" + USAGE
                ) from exc
            if runner_max_iterations < 1:
                raise CompanyCLIError(
                    "--max-iterations must be a positive integer.\n" + USAGE
                )
        else:
            raise CompanyCLIError(f"Unknown company daemon option: {token}.\n{USAGE}")
        index += 1

    if not workflow_path:
        raise CompanyCLIError(
            "`aider company daemon` requires --workflow PATH.\n" + USAGE
        )
    return CompanyCLICommand(
        action="daemon",
        dry_plan=dry_run,
        workflow_path=workflow_path,
        once=once,
        status=status,
        run_issue_id=run_issue_id,
        runner_departments=runner_departments,
        runner_max_iterations=runner_max_iterations,
        tracker_type=tracker_type,
        repo=repo,
        watch=watch,
        event_filter=event_filter,
    )


def format_template_list() -> str:
    """Return a human-readable template catalog as a compact table."""

    rows = []
    for template in list_templates():
        skills = ", ".join(template.recommended_skills or ["generalist"])
        rows.append(
            (
                template.key,
                template.description,
                skills,
                f'aider company new "Build my MVP" --template {template.key}',
            )
        )

    headers = ("Name", "Description", "Recommended Skills", "Example Command")
    widths = [
        max(len(str(row[index])) for row in (headers, *rows))
        for index in range(len(headers))
    ]

    def render_row(row: tuple[str, str, str, str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    lines = [
        "Aider Plus zero-to-MVP templates:",
        render_row(headers),
        "-+-".join("-" * width for width in widths),
    ]
    lines.extend(render_row(row) for row in rows)
    lines.extend(
        [
            "",
            "Template Aliases: old template names still work, but are deprecated and resolve to the canonical names shown above.",
        ]
    )
    return "\n".join(lines)


def render_company_plan(command: CompanyCLICommand) -> str:
    """Render the execution prompt for a parsed create command."""

    avoided_mismatches = tuple(
        reason for reason in command.template_selection_reasons if "custom" in reason.lower() or "mismatch" in reason.lower()
    )
    return render_zero_to_mvp_prompt(
        idea=command.idea,
        template_key=command.template,
        project_name=command.project_name,
        decision_reasons=command.template_selection_reasons,
        avoided_mismatches=avoided_mismatches,
        memory_evidence_ids=command.template_selection_memory_ids,
    )


def handle_company_cli_pre_coder(command: CompanyCLICommand) -> int | None:
    """Handle company commands that do not need a Coder instance."""

    if command.action in {"init", "setup"}:
        return handle_company_onboarding_cli(command)
    if command.action == "templates":
        print(format_template_list())
        return 0
    if command.action == "daemon":
        return handle_company_daemon_cli(command)
    if command.action in {"memory-status", "memory-repair", "memory-backfill"}:
        store = MemoryStore(ProjectMemory(str(Path.cwd())))
        if command.action == "memory-status":
            print(store.get_metrics())
            return 0
        if command.action == "memory-backfill":
            print(store.backfill_legacy_records())
            return 0
        result = store.repair(confirm=command.yes)
        print(result if command.yes else {"dry_run": True, **result})
        return 0
    if command.action in {"create", "new"} and command.dry_plan:
        if command.action == "new":
            warehouse = (
                Path(command.warehouse_path).expanduser().resolve()
                if command.warehouse_path
                else default_warehouse_path()
            )
            product_name = (
                command.project_name or slugify_product_name(command.idea)[:48]
            )
            print(f"Warehouse: {warehouse}")
            print(f"Products dir: {warehouse / 'products'}")
            print(
                f"Product repo: {warehouse / 'products' / slugify_product_name(product_name)}"
            )
        if command.template_alias_note:
            print(command.template_alias_note)
        if command.template_selection_note:
            print(command.template_selection_note)
        print(render_company_plan(command))
        return 0
    return None


def handle_company_onboarding_cli(command: CompanyCLICommand) -> int:
    """Run Company Mode onboarding without constructing a Coder."""

    from aider.company.onboarding import CompanyOnboarding, DEPARTMENTS

    defaults: dict[str, object] = {
        "warehouse_path": command.warehouse_path or default_warehouse_path(),
        "template": command.template,
        "github_repo": command.repo or "",
        "github_token": command.github_token or "",
        "mcp_enabled": bool(command.mcp_enabled),
    }
    if command.model:
        defaults["model"] = command.model
    if command.first_product_idea:
        defaults["first_product_idea"] = command.first_product_idea
    if command.first_product_name:
        defaults["first_product_name"] = command.first_product_name
    if command.yes:
        defaults["model_preferences"] = {
            dept: {"model": command.model or "", "cache": True} for dept in DEPARTMENTS
        }
        defaults["first_product_now"] = True
        prompts = iter(["", "", *("" for _ in range(20))])
        input_func = lambda _prompt: next(prompts, "")
    else:
        input_func = None
    onboarding = CompanyOnboarding(defaults=defaults, input_func=input_func)
    result = onboarding.run_onboarding_flow()
    print(f"Company onboarding config: {result.config_path}")
    print(f"Workflow guide: {result.workflow_guide_path}")
    print(f"Daemon workflow: {result.daemon_workflow_path}")
    return 0


def _load_daemon_for_command(command: CompanyCLICommand) -> CompanyDaemon:
    """Load a daemon and apply CLI tracker overrides."""

    if not command.workflow_path:
        raise CompanyCLIError("`aider company daemon` requires --workflow PATH.")
    if not command.tracker_type and not command.repo:
        return load_daemon(command.workflow_path)

    from aider.company.workflow import CompanyWorkflow

    loaded = CompanyWorkflow.load(command.workflow_path)
    tracker = loaded.tracker
    tracker = TrackerWorkflowConfig(
        kind=command.tracker_type or tracker.kind,
        path=tracker.path,
        repo=command.repo or tracker.repo,
        labels=tracker.labels,
        github=tracker.github,
    )
    return CompanyDaemon(workflow=replace(loaded, tracker=tracker))


def handle_company_daemon_cli(command: CompanyCLICommand) -> int:
    """Run Symphony-inspired Company daemon commands that do not need a Coder."""

    if not command.workflow_path:
        raise CompanyCLIError("`aider company daemon` requires --workflow PATH.")
    try:
        daemon = _load_daemon_for_command(command)
        daemon.configure_runner_options(
            departments=command.runner_departments,
            max_iterations=command.runner_max_iterations,
            dry_run=command.dry_plan,
        )
        unsubscribe = None
        if command.watch:
            from aider.company.surface_messages import format_runtime_event_message

            def _print_event(event):
                if command.event_filter and event.event_type != command.event_filter:
                    return
                print(format_runtime_event_message(event, ansi=True), flush=True)

            event_bus = getattr(
                getattr(daemon, "orchestrator", None), "event_bus", None
            )
            if event_bus is None:
                # Force lazy default runner construction so the daemon and runner share a bus.
                event_bus = getattr(
                    daemon._get_default_runner().orchestrator, "event_bus", None
                )
            if event_bus is not None:
                unsubscribe = event_bus.subscribe(_print_event)
        if command.status:
            status = daemon.status()
            print(f"Workflow: {status['workflow']}")
            print(f"Tracker: {status['tracker']}")
            print(f"Workspace root: {status['workspace_root']}")
            print(f"Max concurrent agents: {status['max_concurrent_agents']}")
            retry_stats = status.get("retry_stats") or {}
            print(
                "Retry stats: "
                f"total_retries={retry_stats.get('total_retries', 0)} "
                f"retrying_runs={retry_stats.get('retrying_runs', 0)} "
                f"last_error={retry_stats.get('last_error') or 'none'}"
            )
            print(f"Last proof link: {status.get('last_proof_link') or 'none'}")
            tracker_status = status.get("tracker_status") or {}
            if tracker_status:
                print(
                    "Tracker retry stats: "
                    f"retry_count={tracker_status.get('retry_count', 0)} "
                    f"last_error={tracker_status.get('last_error') or 'none'}"
                )
            recent = status.get("recent_proof_of_work") or []
            if recent:
                print("Recent proof-of-work:")
                for proof in recent[:5]:
                    print(
                        f"- {proof.get('issue', 'unknown')}: {proof.get('summary', '')} "
                        f"proof={proof.get('markdown_path') or proof.get('path', '')}"
                    )
            if not status["runs"]:
                print("Runs: none")
            else:
                print("Runs:")
                for run in status["runs"]:
                    print(
                        f"- {run.get('issue_id', 'unknown')}: {run.get('status', 'unknown')} "
                        f"attempts={run.get('attempts', 0)} "
                        f"last_error={run.get('last_error') or 'none'} "
                        f"proof={run.get('last_proof_link') or run.get('proof_path') or 'none'} "
                        f"workspace={run.get('workspace', '')}"
                    )
            return 0
        if command.run_issue_id:
            import asyncio

            proofs = [
                asyncio.run(
                    daemon.run_issue(command.run_issue_id, dry_run=command.dry_plan)
                )
            ]
        else:
            proofs = daemon.run_once(dry_run=command.dry_plan)
        if not proofs:
            print("No eligible company daemon issues found.")
            return 0
        if unsubscribe is not None:
            unsubscribe()
        for proof in proofs:
            print(f"Issue: {proof.issue}")
            print(f"Workspace: {proof.workspace}")
            print(f"Summary: {proof.summary}")
            print(
                "Proof of work: "
                f"{Path(proof.workspace) / '.aider' / 'company' / 'proof-of-work.json'}"
            )
            print(f"Partial success: {proof.partial_success}")
            print(f"Retry count: {proof.retry_count}")
            if proof.last_error:
                print(f"Last error: {proof.last_error}")
            if proof.completed_stages:
                print("Completed stages: " + ", ".join(proof.completed_stages))
            if proof.failed_stages:
                print("Failed stages: " + ", ".join(proof.failed_stages))
            print(f"Human review required: {proof.human_review_required}")
        if not command.once:
            print(
                "Processed one daemon tick. Re-run the command or a scheduler for more ticks."
            )
        return 0
    except (CompanyDaemonError, WorkflowError, OSError, ValueError) as exc:
        print(str(exc))
        return 1


def run_company_cli_with_coder(command: CompanyCLICommand, coder) -> int:
    """Run a parsed company command after normal Aider setup creates a coder."""

    if command.action not in {"create", "new"}:
        raise CompanyCLIError(
            f"Unsupported post-coder company command: {command.action}"
        )

    prompt = render_company_plan(command)
    label = (
        "zero-to-MVP new product" if command.action == "new" else "zero-to-MVP create"
    )
    coder.io.tool_output(f"\n🚀 Aider Plus Company: {label}")
    coder.io.tool_output(f"Template: {command.template}")
    if command.template_alias_note:
        coder.io.tool_output(command.template_alias_note)
    if command.template_selection_note:
        coder.io.tool_output(command.template_selection_note)
    if command.project_name:
        coder.io.tool_output(f"Project: {command.project_name}")
    if command.product_path:
        coder.io.tool_output(f"Product repo: {command.product_path}")
    coder.io.tool_output(
        "Routing the product brief through Aider's repo-aware implementation loop.\n"
    )
    # TODO(2026-06-30): Remove direct surface execution fallback once all
    # company surfaces use orchestrator-native execution under run_company_task.
    # Explicit trigger: fallback is used when no orchestrator runtime is attached
    # to CLI sessions (current default behavior).
    from aider.company.schemas import CompanyTask

    def _dirty_files() -> list[str]:
        repo = getattr(coder, "repo", None)
        if repo is None:
            return []
        try:
            out = repo.repo.git.status("--short")
        except Exception:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    async def _execute(task, _metadata):
        exhausted_before = int(getattr(coder, "num_exhausted_context_windows", 0) or 0)
        dirty_before = _dirty_files()

        content = coder.run(with_message=str(task.payload))

        exhausted_after = int(getattr(coder, "num_exhausted_context_windows", 0) or 0)
        if exhausted_after > exhausted_before:
            dirty_after = _dirty_files()
            if dirty_after and dirty_after != dirty_before:
                coder.io.tool_warning(
                    "Model output was truncated by token limits and left uncommitted edits. "
                    "Review with `git status`/`git diff` before continuing."
                )
            else:
                coder.io.tool_warning(
                    "Model output was truncated by token limits. Retrying once with a "
                    "continuation prompt to complete the remaining changes."
                )
                continuation = (
                    str(task.payload)
                    + "\n\nContinue from where you stopped. Keep the response short and only "
                    "emit the next minimal patch needed to finish."
                )
                content = coder.run(with_message=continuation)

        return {"summary": str(content or ""), "status": "success"}

    def _collect_rewrite_signals() -> tuple[list[str], dict[str, dict[str, int]], str]:
        repo = getattr(coder, "repo", None)
        if repo is None:
            return [], {}, "no_repo"
        try:
            output = repo.repo.git.diff("--numstat", "HEAD")
        except Exception:
            return [], {}, "diff_unavailable"
        heavy: list[str] = []
        stats: dict[str, dict[str, int]] = {}
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added_raw, deleted_raw, path = parts[0], parts[1], parts[2]
            try:
                added = int(added_raw)
                deleted = int(deleted_raw)
            except ValueError:
                continue
            stats[path] = {"added": added, "deleted": deleted}
            if added + deleted >= 80:
                heavy.append(path)
        return heavy, stats, "ok"

    import asyncio

    request = CompanyRunRequest(
        surface="cli",
        session_id="cli",
        task=CompanyTask(
            task_id="cli-company-create",
            origin="ceo",
            target="engineering",
            artifact_type="raw_prompt",
            payload=prompt,
            blocking=False,
        ),
    )
    run_result = asyncio.run(run_company_task(request, execute=_execute))

    selected = command.template
    confidence = None
    if command.template_selection_note:
        import re

        match = re.search(r"confidence\s+([0-9]+(?:\.[0-9]+)?)", command.template_selection_note)
        if match:
            try:
                confidence = float(match.group(1))
            except ValueError:
                confidence = None
    status_value = str((run_result or {}).get("status") or "success").lower()
    accepted = status_value == "success"
    outcome = "success" if accepted else "failure"
    heavy_files, rewrite_stats, rewrite_status = _collect_rewrite_signals()
    feedback = {
        "selected_template": selected,
        "confidence": confidence,
        "accepted": accepted,
        "outcome": outcome,
        "heavily_rewritten_files": heavy_files,
        "pass_fail_outcome": status_value,
        "rewrite_detection_status": rewrite_status,
    }
    store = MemoryStore(ProjectMemory(str(Path.cwd())))
    record = store.append_record(
        MemoryRecord(
            content=f"Template selection reflection for {command.action}: {selected}",
            kind="reflection",
            scope="project",
            metadata={
                "source": "company_cli_post_run_reflection",
                "template_key": selected,
                "selection_confidence": confidence,
                "accepted": accepted,
                "outcome": outcome,
                "pass_fail_outcome": status_value,
                "heavily_rewritten_files": heavy_files,
                "rewrite_stats": rewrite_stats,
                "idea": command.idea,
                "project_name": command.project_name or "",
            },
            skill_evidence={"role": "engineering", "outcome": outcome},
        )
    )
    record_skill_outcome(
        store,
        skill_name="template-selection",
        scope="engineering",
        task_id=f"cli-{command.action}",
        outcome=outcome,
        supporting_memory_ids=[record.id, *list(command.template_selection_memory_ids)],
    )
    for memory_id in command.template_selection_memory_ids:
        record_memory_outcome(
            store,
            record_id=memory_id,
            outcome=outcome,
            related_skill_ids=["engineering:template-selection"],
        )
    try:
        state = CompanyStateManager(Path.cwd())
        service = SelfImprovementService(state)
        reinforcement = service.apply_reinforcement_and_decay()
        feedback["reinforcement"] = reinforcement
    except Exception:
        feedback["reinforcement"] = {"decayed_records": 0, "review_candidates": []}
    coder.io.tool_output(
        f"Recorded template reflection outcome: {outcome}; "
        f"heavy rewrites={len(heavy_files)}."
    )
    return 0


@dataclass(frozen=True)
class WarehouseCLICommand:
    """Parsed `aider warehouse ...` command."""

    action: str
    warehouse_path: str | None = None
    product: str | None = None


def parse_warehouse_cli(
    argv: Sequence[str],
) -> tuple[WarehouseCLICommand | None, list[str]]:
    """Parse standalone warehouse commands that do not require a Coder."""

    args = list(argv)
    if not args or args[0] != "warehouse":
        return None, args
    if len(args) == 1 or args[1] in {"-h", "--help", "help"}:
        raise CompanyCLIError(USAGE)

    action = args[1]
    rest = args[2:]
    warehouse_path: str | None = None
    product: str | None = None

    if action == "init":
        if rest:
            if len(rest) > 1:
                raise CompanyCLIError(
                    "`aider warehouse init` accepts at most one path.\n" + USAGE
                )
            warehouse_path = rest[0]
        return WarehouseCLICommand(action=action, warehouse_path=warehouse_path), []

    if action in {"list", "status"}:
        warehouse_path = _parse_optional_warehouse(rest)
        return WarehouseCLICommand(action=action, warehouse_path=warehouse_path), []

    if action == "open":
        if not rest:
            raise CompanyCLIError(
                "`aider warehouse open` requires a product name or slug.\n" + USAGE
            )
        product = rest[0]
        warehouse_path = _parse_optional_warehouse(rest[1:])
        return (
            WarehouseCLICommand(
                action=action, warehouse_path=warehouse_path, product=product
            ),
            [],
        )

    raise CompanyCLIError(f"Unknown warehouse command: {action}\n{USAGE}")


def _parse_optional_warehouse(args: Sequence[str]) -> str | None:
    rest = list(args)
    if not rest:
        return None
    if len(rest) == 2 and rest[0] == "--warehouse":
        return rest[1]
    raise CompanyCLIError("Expected optional `--warehouse PATH`.\n" + USAGE)


def handle_warehouse_cli(command: WarehouseCLICommand) -> int:
    """Run standalone warehouse commands."""

    try:
        manager = WarehouseManager(command.warehouse_path)
        if command.action == "init":
            manager.init()
            print(f"Warehouse initialized at {manager.root}")
            print(f"Registry: {manager.registry_path}")
            print(f"Products dir: {manager.products_dir}")
            return 0
        if command.action == "list":
            products = manager.list_products()
            if not products:
                print(f"No products registered in {manager.root}")
                return 0
            for product in products:
                print(
                    f"- {product.slug}: {product.name} [{product.template or 'no-template'}] {product.path}"
                )
            return 0
        if command.action == "status":
            status = manager.status()
            print(f"Warehouse: {status['root']}")
            print(f"Registry: {status['registry']}")
            print(f"Products dir: {status['products_dir']}")
            print(
                f"Products: {status['products']} ({status['existing_products']} existing, {status['missing_products']} missing)"
            )
            print(f"COO memory: {status['coo_memory']}")
            return 0
        if command.action == "open":
            product = manager.get_product(command.product or "")
            print(product.path)
            return 0
    except WarehouseError as exc:
        print(str(exc))
        return 1

    print(f"Unsupported warehouse command: {command.action}")
    return 1


def prepare_company_workspace(command: CompanyCLICommand) -> None:
    """Create/switch to a product repo for `aider company new` before Coder setup."""

    if command.action != "new" or command.dry_plan:
        return

    product_name = command.project_name or slugify_product_name(command.idea)[:48]
    manager = WarehouseManager(command.warehouse_path)
    try:
        record = manager.create_product(
            name=product_name,
            idea=command.idea,
            template=command.template,
        )
    except WarehouseError as exc:
        raise CompanyCLIError(str(exc)) from exc
    object.__setattr__(command, "project_name", record.name)
    object.__setattr__(command, "product_path", record.path)
    os.chdir(record.path)
    print(f"Warehouse: {manager.root}")
    print(f"Products dir: {manager.products_dir}")
    print(f"Product repo: {record.path}")


# Backwards-compatible alias for docs/tests that still speak in terms of create.
company_workspace_path = default_warehouse_path
