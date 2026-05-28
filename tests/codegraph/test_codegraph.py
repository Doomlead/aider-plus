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
