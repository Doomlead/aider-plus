import json

from aider.company.cli import CompanyCLIError, format_template_list, parse_company_cli
from aider.company.templates import (
    get_template,
    render_template_starter_files,
    render_zero_to_mvp_prompt,
)
from aider.company.warehouse import WarehouseManager


def test_template_catalog_lists_hardened_product_templates():
    catalog = format_template_list()

    for key in [
        "nextjs-saas",
        "python-fastapi-api",
        "electron-desktop-app",
        "data-dashboard",
        "data-dashboard-streamlit",
        "cli-tool-python",
    ]:
        assert key in catalog

    assert "skills:" in catalog
    assert "PRD seed:" in catalog


def test_template_model_exposes_richer_metadata():
    template = get_template("nextjs-saas")

    assert "frontend" in template.recommended_skills
    assert "README.md" in template.starter_files
    assert template.post_creation_instructions
    assert template.qa_gates
    assert "PRD" in template.example_prd_prompt


def test_render_template_starter_files_generates_company_scaffold():
    files = render_template_starter_files(
        idea="Build revenue analytics for founders",
        template_key="data-dashboard",
        project_name="Founder Metrics",
        project_slug="founder-metrics",
    )

    product = json.loads(files[".aider/company/product.json"])
    assert product["template"] == "data-dashboard"
    assert "data" in product["recommended_skills"]
    assert product["qa_gates"]
    assert files["README.md"].startswith("# Founder Metrics")
    assert "Product → UX" in files["docs/company-mode.md"]
    assert "Metric definitions" in files["src/metrics/README.md"]
    assert "## QA Gates" in files["docs/product-brief.md"]
    assert ".aider/skills/data/SKILL.md" in files


def test_prompt_enhancement_injects_template_guidance():
    prompt = render_zero_to_mvp_prompt(
        idea="Build a secure note-taking desktop app",
        template_key="electron-desktop-app",
        project_name="Secure Notes",
    )

    assert "Product template: Electron desktop app (electron-desktop-app)" in prompt
    assert "Recommended skills to activate or emulate" in prompt
    assert "secure IPC" in prompt
    assert "Post-creation instructions" in prompt
    assert "Product -> UX -> Engineering -> Delivery -> DevOps" in prompt


def test_company_new_validates_template_name_during_parse():
    try:
        parse_company_cli(["company", "new", "Build x", "--template", "nope"])
    except CompanyCLIError as exc:
        assert "Unknown project template" in str(exc)
    else:
        raise AssertionError("Expected invalid template to fail parse")


def test_warehouse_applies_template_post_creation_hooks(tmp_path):
    manager = WarehouseManager(tmp_path / "warehouse")
    record = manager.create_product(
        name="Desktop Notes",
        idea="Build secure notes",
        template="electron-desktop-app",
    )

    product_path = tmp_path / "warehouse" / "products" / "desktop-notes"
    assert record.template == "electron-desktop-app"
    assert product_path.joinpath(".aider", "company", "post-creation.md").exists()
    assert product_path.joinpath("electron", "preload", "README.md").exists()
    assert product_path.joinpath(".aider", "skills", "desktop", "SKILL.md").exists()
    assert product_path.joinpath(".git").exists()


def test_new_high_value_templates_seed_expected_files():
    streamlit_files = render_template_starter_files(
        idea="Build a KPI dashboard",
        template_key="data-dashboard-streamlit",
        project_name="KPI Studio",
        project_slug="kpi-studio",
    )
    assert "app.py" in streamlit_files
    assert "src/kpi_studio/metrics/README.md" in streamlit_files
    assert ".aider/skills/python/SKILL.md" in streamlit_files

    cli_files = render_template_starter_files(
        idea="Build a report exporter",
        template_key="cli-tool-python",
        project_name="Report Exporter",
        project_slug="report-exporter",
    )
    assert "src/report_exporter/cli.py" in cli_files
    assert "src/report_exporter/config.py" in cli_files
    assert "exit-code" in cli_files["tests/README.md"]
