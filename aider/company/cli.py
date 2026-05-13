"""CLI helpers for the Aider Plus Company golden path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from aider.company.templates import (
    DEFAULT_TEMPLATE_KEY,
    list_templates,
    render_zero_to_mvp_prompt,
)


@dataclass(frozen=True)
class CompanyCLICommand:
    """Parsed `aider company ...` command."""

    action: str
    idea: str = ""
    template: str = DEFAULT_TEMPLATE_KEY
    project_name: str | None = None
    dry_plan: bool = False


class CompanyCLIError(ValueError):
    """Raised for invalid `aider company` invocations."""


USAGE = """Usage:
  aider company templates
  aider company create <idea> [--template TEMPLATE] [--name PROJECT_NAME] [--dry-plan] [-- AIDER_ARGS...]

Examples:
  aider company templates
  aider company create "Build a habit tracker web app with login, dashboard, and streaks"
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

    if action != "create":
        raise CompanyCLIError(f"Unknown company command: {action}\n{USAGE}")

    template = DEFAULT_TEMPLATE_KEY
    project_name: str | None = None
    dry_plan = False
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
        elif token == "--dry-plan":
            dry_plan = True
        elif token.startswith("--"):
            raise CompanyCLIError(
                f"Unknown company create option: {token}. Put Aider options after `--`.\n{USAGE}"
            )
        else:
            idea_parts.append(token)
        index += 1

    idea = " ".join(idea_parts).strip()
    if not idea:
        raise CompanyCLIError(
            "`aider company create` requires a product idea.\n" + USAGE
        )

    return (
        CompanyCLICommand(
            action="create",
            idea=idea,
            template=template,
            project_name=project_name,
            dry_plan=dry_plan,
        ),
        aider_args,
    )


def format_template_list() -> str:
    """Return a human-readable template catalog."""

    lines = ["Aider Plus zero-to-MVP templates:"]
    for template in list_templates():
        lines.append(f"- {template.summary()}")
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
    if command.action == "create" and command.dry_plan:
        print(render_company_plan(command))
        return 0
    return None


def run_company_cli_with_coder(command: CompanyCLICommand, coder) -> int:
    """Run a parsed company command after normal Aider setup creates a coder."""

    if command.action != "create":
        raise CompanyCLIError(
            f"Unsupported post-coder company command: {command.action}"
        )

    prompt = render_company_plan(command)
    coder.io.tool_output("\n🚀 Aider Plus Company: zero-to-MVP create")
    coder.io.tool_output(f"Template: {command.template}")
    if command.project_name:
        coder.io.tool_output(f"Project: {command.project_name}")
    coder.io.tool_output(
        "Routing the product brief through Aider's repo-aware implementation loop.\n"
    )
    coder.run(with_message=prompt)
    return 0
