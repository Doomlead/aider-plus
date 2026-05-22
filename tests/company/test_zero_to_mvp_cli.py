import pytest

from aider.company.cli import (
    CompanyCLIError,
    format_template_list,
    parse_company_cli,
    render_company_plan,
)
from aider.company.templates import get_template, render_zero_to_mvp_prompt


def test_company_templates_catalog_includes_requested_product_shapes():
    catalog = format_template_list()

    for key in [
        "nextjs-saas",
        "fastapi-api",
        "python-cli",
        "electron-desktop",
        "streamlit-dashboard",
        "data-dashboard",
        "discord-bot",
        "browser-extension",
        "internal-admin",
    ]:
        assert key in catalog


def test_parse_company_create_strips_aider_args_after_double_dash():
    command, aider_args = parse_company_cli(
        [
            "company",
            "create",
            "Build",
            "a",
            "Stripe",
            "webhook",
            "API",
            "--template",
            "fastapi-api",
            "--name",
            "Billing Hooks",
            "--",
            "--model",
            "gpt-5.5",
        ]
    )

    assert command.action == "create"
    assert command.idea == "Build a Stripe webhook API"
    assert command.template == "fastapi-api"
    assert command.project_name == "Billing Hooks"
    assert aider_args == ["--model", "gpt-5.5"]


def test_company_create_dry_plan_renders_iteration_focused_brief():
    command, _ = parse_company_cli(
        [
            "company",
            "create",
            "Build a habit tracker with streaks",
            "--template",
            "nextjs-saas",
            "--dry-plan",
        ]
    )
    plan = render_company_plan(command)

    assert command.dry_plan is True
    assert "Build a habit tracker with streaks" in plan
    assert "Product template: Next.js SaaS (nextjs-saas)" in plan
    assert "Product -> UX -> Engineering -> QA -> DevOps" in plan
    assert "The goal is not a one-shot code dump" in plan
    assert "keep evolving" in plan


def test_unknown_template_raises_clear_error():
    with pytest.raises(ValueError, match="Unknown project template"):
        get_template("unknown-template")


def test_company_help_is_parse_error_with_usage():
    with pytest.raises(CompanyCLIError, match="Usage:"):
        parse_company_cli(["company", "help"])


def test_company_memory_backfill_parses():
    command, aider_args = parse_company_cli(["company", "memory", "backfill"])
    assert command.action == "memory-backfill"
    assert aider_args == []


def test_company_create_without_template_uses_repo_detection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    command, _ = parse_company_cli(["company", "create", "Build", "a", "novel", "tool"])
    assert command.template == "repo-native"


def test_render_zero_to_mvp_prompt_has_template_specific_quality_gates():
    prompt = render_zero_to_mvp_prompt(
        idea="Build an internal refund approval tool",
        template_key="internal-admin",
        project_name="RefundOps",
    )

    assert "Project name: RefundOps" in prompt
    assert "destructive actions" in prompt
    assert "approval preferences" in prompt
    assert (
        "Summarize the Product, UX, Engineering, QA, release, and post-mortem outcomes"
        in prompt
    )
