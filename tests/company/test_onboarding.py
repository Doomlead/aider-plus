import json
from pathlib import Path

from aider.company.cli import handle_company_cli_pre_coder, parse_company_cli
from aider.company.onboarding import CompanyOnboarding, DEPARTMENTS


def _blank_prompts():
    while True:
        yield ""


def test_onboarding_flow_writes_state_workflow_and_guide(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    prompts = _blank_prompts()
    defaults = {
        "warehouse_path": tmp_path / "warehouse",
        "template": "nextjs-saas",
        "github_repo": "octo/demo",
        "github_token": "gh-test",
        "mcp_enabled": True,
        "model_preferences": {
            dept: {"model": "gpt-5.5", "cache": True} for dept in DEPARTMENTS
        },
    }
    onboarding = CompanyOnboarding(
        root=tmp_path,
        defaults=defaults,
        input_func=lambda _prompt: next(prompts),
        output_func=lambda _msg: None,
    )

    result = onboarding.run_onboarding_flow()

    assert Path(result.config_path).exists()
    assert Path(result.workflow_guide_path).exists()
    assert Path(result.daemon_workflow_path).exists()
    assert Path(result.env_example_path).exists()
    assert (tmp_path / "warehouse" / "warehouse.json").exists()

    state = json.loads(Path(result.config_path).read_text(encoding="utf-8"))
    assert state["default_template"] == "nextjs-saas"
    assert state["github_repo"] == "octo/demo"
    assert state["github_token_configured"] is True
    assert state["mcp_enabled"] is True
    assert state["api_key_validation"] == {"OPENAI_API_KEY": False}
    assert state["model_preferences"]["engineering"]["model"] == "gpt-5.5"

    guide = Path(result.workflow_guide_path).read_text(encoding="utf-8")
    assert "aider company new" in guide
    assert "aider company daemon" in guide
    assert "octo/demo" in guide

    env_example = Path(result.env_example_path).read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=your_openai_api_key_here" in env_example
    assert "GITHUB_TOKEN=ghp_your_token_here" in env_example
    assert "AIDER_MCP_CONFIG=.aider/mcp.json" in env_example


def test_onboarding_flow_prompts_department_models(tmp_path):
    answers = iter(
        [
            "",  # warehouse default
            "fastapi-api",
            "",  # github repo
            "",  # github token
            *sum(([f"model-{dept}", "n"] for dept in DEPARTMENTS), []),
            "y",  # mcp
            "n",  # first product
        ]
    )
    onboarding = CompanyOnboarding(
        root=tmp_path,
        defaults={"warehouse_path": tmp_path / "warehouse", "advanced": True},
        input_func=lambda _prompt: next(answers),
        output_func=lambda _msg: None,
    )

    result = onboarding.run_onboarding_flow()

    assert result.template == "fastapi-api"
    assert result.mcp_enabled is True
    assert result.model_preferences["product"] == {
        "model": "model-product",
        "cache": False,
    }
    assert result.model_preferences["devops"] == {
        "model": "model-devops",
        "cache": False,
    }


def test_company_init_cli_parses_and_runs_non_interactive(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    command, aider_args = parse_company_cli(
        [
            "company",
            "init",
            "--warehouse",
            str(tmp_path / "warehouse"),
            "--template",
            "nextjs-saas",
            "--github-repo",
            "octo/demo",
            "--model",
            "gpt-5.5",
            "--enable-mcp",
            "--product-idea",
            "Build a CLI dashboard",
            "--product-name",
            "CLI Dashboard",
            "--yes",
        ]
    )

    assert aider_args == []
    assert command.action == "init"
    assert command.yes is True
    assert handle_company_cli_pre_coder(command) == 0

    out = capsys.readouterr().out
    assert "Company onboarding config" in out
    assert (tmp_path / "AIDER_WORKFLOW.md").exists()
    state = json.loads(
        (tmp_path / ".aider" / "company" / "onboarding.json").read_text()
    )
    assert state["github_repo"] == "octo/demo"
    assert state["model_preferences"]["qa"]["model"] == "gpt-5.5"
    assert Path(state["first_product_path"]).name == "cli-dashboard"
    assert Path(state["first_product_path"]).joinpath(".git").exists()
    assert (tmp_path / ".env.example").exists()


def test_minimal_onboarding_uses_env_defaults_and_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", "env/demo")
    monkeypatch.setenv("AIDER_MODEL", "gpt-5.5")
    monkeypatch.setenv("AIDER_MCP_CONFIG", ".aider/mcp.json")
    answers = iter(["", "", "", "n"])
    prompts = []
    output = []

    def input_func(prompt):
        prompts.append(prompt)
        return next(answers)

    onboarding = CompanyOnboarding(
        root=tmp_path,
        defaults={"warehouse_path": tmp_path / "warehouse"},
        input_func=input_func,
        output_func=output.append,
    )

    result = onboarding.run_onboarding_flow()

    assert len(prompts) == 4
    assert any("Step 1 of 5" in msg for msg in output)
    assert any("Step 5 of 5" in msg for msg in output)
    state = json.loads(Path(result.config_path).read_text(encoding="utf-8"))
    assert state["onboarding_mode"] == "minimal"
    assert state["github_repo"] == "env/demo"
    assert state["mcp_enabled"] is True
    assert state["model_preferences"]["product"] == {"model": "gpt-5.5", "cache": True}
    assert state["model_preferences"]["devops"] == {"model": "gpt-5.5", "cache": True}


def test_company_init_cli_accepts_advanced_flag():
    command, aider_args = parse_company_cli(["company", "init", "--advanced"])

    assert aider_args == []
    assert command.advanced is True
