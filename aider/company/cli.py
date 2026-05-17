"""CLI helpers for the Aider Plus Company golden path."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from aider.company.daemon import CompanyDaemonError, load_daemon
from aider.company.workflow import WorkflowError
from aider.company.templates import (
    DEFAULT_TEMPLATE_KEY,
    get_template,
    list_templates,
    render_zero_to_mvp_prompt,
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


class CompanyCLIError(ValueError):
    """Raised for invalid `aider company` invocations."""


USAGE = """Usage:
  aider company templates
  aider company create <idea> [--template TEMPLATE] [--name PROJECT_NAME] [--dry-plan] [-- AIDER_ARGS...]
  aider company new <idea> [--template TEMPLATE] [--name PRODUCT_NAME] [--warehouse PATH] [--dry-plan] [-- AIDER_ARGS...]
  aider company daemon --workflow PATH [--once] [--dry-run] [--status] [--run ISSUE_ID] [--departments LIST] [--max-iterations N]
  aider warehouse init [PATH]
  aider warehouse list [--warehouse PATH]
  aider warehouse open PRODUCT [--warehouse PATH]
  aider warehouse status [--warehouse PATH]

Examples:
  aider company templates
  aider company create "Build a habit tracker web app with login, dashboard, and streaks"
  aider company new "Build a habit tracker" --name habit-tracker --template nextjs-app
  aider company create "Build a Stripe webhook API" --template fastapi-backend -- --model gpt-5.5
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

    if action == "templates":
        if rest:
            raise CompanyCLIError(
                "`aider company templates` does not accept extra arguments.\n" + USAGE
            )
        return CompanyCLICommand(action="templates"), aider_args

    if action == "daemon":
        return _parse_company_daemon(rest), aider_args

    if action not in {"create", "new"}:
        raise CompanyCLIError(f"Unknown company command: {action}\n{USAGE}")

    template = DEFAULT_TEMPLATE_KEY
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
    try:
        template = get_template(template).key
    except ValueError as exc:
        raise CompanyCLIError(str(exc) + "\n" + USAGE) from exc

    return (
        CompanyCLICommand(
            action=action,
            idea=idea,
            template=template,
            project_name=project_name,
            dry_plan=dry_plan,
            warehouse_path=warehouse_path,
        ),
        aider_args,
    )


def _parse_company_daemon(args: Sequence[str]) -> CompanyCLICommand:
    workflow_path: str | None = None
    dry_run = False
    once = False
    status = False
    run_issue_id: str | None = None
    runner_departments: tuple[str, ...] = ()
    runner_max_iterations: int | None = None
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
        elif token == "--once":
            once = True
        elif token == "--status":
            status = True
        elif token == "--run":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--run requires an issue id.\n" + USAGE)
            run_issue_id = rest[index]
        elif token == "--departments":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--departments requires a comma-separated list.\n" + USAGE)
            runner_departments = tuple(
                item.strip().lower() for item in rest[index].split(",") if item.strip()
            )
        elif token == "--max-iterations":
            index += 1
            if index >= len(rest):
                raise CompanyCLIError("--max-iterations requires a positive integer.\n" + USAGE)
            try:
                runner_max_iterations = int(rest[index])
            except ValueError as exc:
                raise CompanyCLIError("--max-iterations must be a positive integer.\n" + USAGE) from exc
            if runner_max_iterations < 1:
                raise CompanyCLIError("--max-iterations must be a positive integer.\n" + USAGE)
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
    )


def format_template_list() -> str:
    """Return a human-readable template catalog."""

    lines = ["Aider Plus zero-to-MVP templates:"]
    for template in list_templates():
        lines.append(f"- {template.summary()}")
        if template.post_creation_instructions:
            lines.append(
                "  post-create: " + "; ".join(template.post_creation_instructions[:2])
            )
        if template.example_prd_prompt:
            lines.append(f"  PRD seed: {template.example_prd_prompt}")
    return "\n".join(lines)


def render_company_plan(command: CompanyCLICommand) -> str:
    """Render the execution prompt for a parsed create command."""

    return render_zero_to_mvp_prompt(
        idea=command.idea,
        template_key=command.template,
        project_name=command.project_name,
    )


def handle_company_cli_pre_coder(command: CompanyCLICommand) -> int | None:
    """Handle company commands that do not need a Coder instance."""

    if command.action == "templates":
        print(format_template_list())
        return 0
    if command.action == "daemon":
        return handle_company_daemon_cli(command)
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
        print(render_company_plan(command))
        return 0
    return None


def handle_company_daemon_cli(command: CompanyCLICommand) -> int:
    """Run Symphony-inspired Company daemon commands that do not need a Coder."""

    if not command.workflow_path:
        raise CompanyCLIError("`aider company daemon` requires --workflow PATH.")
    try:
        daemon = load_daemon(command.workflow_path)
        daemon.configure_runner_options(
            departments=command.runner_departments,
            max_iterations=command.runner_max_iterations,
            dry_run=command.dry_plan,
        )
        if command.status:
            status = daemon.status()
            print(f"Workflow: {status['workflow']}")
            print(f"Tracker: {status['tracker']}")
            print(f"Workspace root: {status['workspace_root']}")
            print(f"Max concurrent agents: {status['max_concurrent_agents']}")
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
                        f"attempts={run.get('attempts', 0)} workspace={run.get('workspace', '')}"
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
        for proof in proofs:
            print(f"Issue: {proof.issue}")
            print(f"Workspace: {proof.workspace}")
            print(f"Summary: {proof.summary}")
            print(
                "Proof of work: "
                f"{Path(proof.workspace) / '.aider' / 'company' / 'proof-of-work.json'}"
            )
            print(f"Partial success: {proof.partial_success}")
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
    if command.project_name:
        coder.io.tool_output(f"Project: {command.project_name}")
    if command.product_path:
        coder.io.tool_output(f"Product repo: {command.product_path}")
    coder.io.tool_output(
        "Routing the product brief through Aider's repo-aware implementation loop.\n"
    )
    coder.run(with_message=prompt)
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
