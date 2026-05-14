from pathlib import Path

from aider.company.cli import (
    handle_warehouse_cli,
    parse_company_cli,
    parse_warehouse_cli,
    prepare_company_workspace,
)
from aider.company.warehouse import WarehouseManager, slugify_product_name


def test_slugify_product_name_creates_safe_repo_name():
    assert slugify_product_name("Habit Tracker MVP!") == "habit-tracker-mvp"


def test_parse_company_new_accepts_warehouse_and_aider_args():
    command, aider_args = parse_company_cli(
        [
            "company",
            "new",
            "Build",
            "a",
            "habit",
            "tracker",
            "--name",
            "Habit Tracker",
            "--template",
            "nextjs-app",
            "--warehouse",
            "/tmp/products",
            "--",
            "--model",
            "gpt-5.5",
        ]
    )

    assert command.action == "new"
    assert command.idea == "Build a habit tracker"
    assert command.project_name == "Habit Tracker"
    assert command.template == "nextjs-app"
    assert command.warehouse_path == "/tmp/products"
    assert aider_args == ["--model", "gpt-5.5"]


def test_prepare_company_workspace_creates_git_backed_product_repo(
    tmp_path, monkeypatch
):
    start = Path.cwd()
    command, _ = parse_company_cli(
        [
            "company",
            "new",
            "Build a habit tracker",
            "--name",
            "Habit Tracker",
            "--warehouse",
            str(tmp_path / "products"),
        ]
    )

    try:
        prepare_company_workspace(command)
        product_path = tmp_path / "products" / "products" / "habit-tracker"
        assert Path.cwd() == product_path
        assert product_path.joinpath(".git").exists()
        assert product_path.joinpath("docs", "product-brief.md").exists()
        assert product_path.joinpath("src", "app", "README.md").exists()
        assert command.product_path == str(product_path)
        registry = WarehouseManager(tmp_path / "products").get_product("habit-tracker")
        assert registry.name == "Habit Tracker"
        assert registry.idea == "Build a habit tracker"
    finally:
        monkeypatch.chdir(start)


def test_warehouse_cli_init_and_list(tmp_path, capsys):
    command, _ = parse_warehouse_cli(["warehouse", "init", str(tmp_path / "products")])
    assert handle_warehouse_cli(command) == 0

    manager = WarehouseManager(tmp_path / "products")
    manager.create_product(
        name="Billing API", idea="Build billing", template="fastapi-backend"
    )

    command, _ = parse_warehouse_cli(
        ["warehouse", "list", "--warehouse", str(tmp_path / "products")]
    )
    assert handle_warehouse_cli(command) == 0
    out = capsys.readouterr().out
    assert "billing-api" in out
    assert "fastapi-backend" in out


def test_warehouse_root_contains_products_directory(tmp_path):
    manager = WarehouseManager(tmp_path / "AiderPlusWarehouse")
    manager.init()

    record = manager.create_product(
        name="Habit Tracker", idea="Build habits", template="nextjs-app"
    )

    product_path = tmp_path / "AiderPlusWarehouse" / "products" / "habit-tracker"
    assert manager.products_dir == tmp_path / "AiderPlusWarehouse" / "products"
    assert record.path == str(product_path)
    assert product_path.joinpath("README.md").exists()
    assert product_path.joinpath("app", "README.md").exists()
    assert product_path.joinpath(".aider", "company", "product.json").exists()
