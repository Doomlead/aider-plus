from __future__ import annotations

from pathlib import Path

from aider.codegraph import CodeGraph, CodeGraphWatcher
from aider.mcp.server import (
    AiderPlusMCPServer,
    AiderPlusMCPServerConfig,
    list_builtin_mcp_tools,
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_codegraph_indexes_searches_impacts_routes_and_tests(tmp_path):
    write(
        tmp_path / "app.py",
        "from service import greet\n\n@app.get('/hello')\ndef hello():\n    return greet()\n",
    )
    write(tmp_path / "service.py", "def greet():\n    return 'hi'\n")
    write(
        tmp_path / "tests" / "test_app.py",
        "from app import hello\n\ndef test_hello():\n    assert hello() == 'hi'\n",
    )

    graph = CodeGraph(tmp_path)
    result = graph.index(force=True)

    assert result["indexed"] == 3
    assert graph.status().symbols >= 3
    assert any(hit["name"] == "greet" for hit in graph.search("greet"))
    assert any(edge["src_file"] == "app.py" for edge in graph.callers("greet"))
    assert any(edge["dst_file"] == "service.py" for edge in graph.callees("app.py"))
    impact = graph.impact("greet")
    assert "app.py" in {item["path"] for item in impact["files"]}
    assert any(route["route"] == "/hello" for route in graph.context("hello")["routes"])
    affected = graph.affected_tests(["service.py"])
    assert "tests/test_app.py" in affected["affected_tests"]


def test_codegraph_watcher_syncs_incrementally(tmp_path):
    write(tmp_path / "one.py", "def one():\n    return 1\n")
    graph = CodeGraph(tmp_path)
    watcher = CodeGraphWatcher(graph)

    first = watcher.sync_once()
    assert first["indexed"] == 1

    write(tmp_path / "one.py", "def one():\n    return 2\n")
    second = watcher.sync_once()
    assert second["indexed"] == 1


def test_mcp_exposes_codegraph_tools(tmp_path):
    write(tmp_path / "service.py", "def greet():\n    return 'hi'\n")
    tools = {tool["name"] for tool in list_builtin_mcp_tools()}
    assert "codegraph_search" in tools

    server = AiderPlusMCPServer(AiderPlusMCPServerConfig(repo_path=str(tmp_path)))
    assert server.codegraph_search("greet")["results"]


def test_codegraph_detects_framework_file_and_decorator_routes(tmp_path):
    write(
        tmp_path / "urls.py",
        "from django.urls import path\nurlpatterns = [path('teams/<int:pk>/', view)]\n",
    )
    write(
        tmp_path / "controller.ts", "@Get('accounts/:id')\nfindOne() { return true }\n"
    )
    write(
        tmp_path / "app" / "dashboard" / "page.tsx",
        "export default function Page() { return null }\n",
    )
    write(
        tmp_path / "src" / "routes" / "settings" / "+page.svelte", "<h1>Settings</h1>\n"
    )

    graph = CodeGraph(tmp_path)
    graph.index(force=True)

    routes = graph.routes_for_files(
        [
            "urls.py",
            "controller.ts",
            "app/dashboard/page.tsx",
            "src/routes/settings/+page.svelte",
        ]
    )
    route_pairs = {
        (route["framework"], route["method"], route["route"]) for route in routes
    }

    assert ("django", None, "/teams/<int:pk>/") in route_pairs
    assert ("nestjs", "GET", "accounts/:id") in route_pairs
    assert ("nextjs", "GET", "/dashboard") in route_pairs
    assert ("sveltekit", "GET", "/settings") in route_pairs


def test_codegraph_infers_affected_tests_and_commands_without_import_edges(tmp_path):
    write(
        tmp_path / "src" / "billing" / "service.py",
        "def charge():\n    return True\n",
    )
    write(
        tmp_path / "tests" / "billing" / "test_service.py",
        "def test_charge_contract():\n    assert True\n",
    )

    graph = CodeGraph(tmp_path)
    graph.index(force=True)

    affected = graph.affected_tests(["src/billing/service.py"])

    assert affected["confidence"] == "medium"
    assert "tests/billing/test_service.py" in affected["affected_tests"]
    assert affected["suggested_commands"] == [
        "pytest tests/billing/test_service.py -v --tb=short"
    ]
