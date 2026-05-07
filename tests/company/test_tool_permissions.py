"""
Tests that ToolRegistry._authorize correctly enforces department-scoped
tool allowlists and raises ToolPermissionError on violations.
"""

from __future__ import annotations

import pytest

from aider.agent.tools import Tool, ToolPermissionError, ToolRegistry

# ---------------------------------------------------------------------------
# Minimal fake department — mirrors the interface that ToolRegistry reads
# ---------------------------------------------------------------------------


class FakeDepartment:
    def __init__(self, name: str, allowed_tools: list[str]):
        self.name = name
        self.allowed_tools = list(allowed_tools)

    def can_use_tool(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools


def _make_registry(department: FakeDepartment) -> ToolRegistry:
    registry = ToolRegistry(department=department)
    registry.register(
        Tool(
            name="shell",
            description="Run shell commands",
            func=lambda command="": f"ran: {command}",
            parameters={"name": "shell"},
        )
    )
    registry.register(
        Tool(
            name="pytest",
            description="Run pytest",
            func=lambda files="": f"pytest: {files}",
            parameters={"name": "pytest"},
        )
    )
    registry.register(
        Tool(
            name="aider_coder",
            description="Run aider coder",
            func=lambda msg="": f"coded: {msg}",
            parameters={"name": "aider_coder"},
        )
    )
    return registry


# ---------------------------------------------------------------------------
# Permission violation tests
# ---------------------------------------------------------------------------


class TestToolPermissionEnforcement:

    def test_qa_cannot_use_aider_coder(self):
        """QA department must not be able to invoke the engineering tool."""
        qa = FakeDepartment("qa", allowed_tools=["shell", "pytest", "linter"])
        registry = _make_registry(qa)

        with pytest.raises(ToolPermissionError) as exc_info:
            registry.execute("aider_coder", {"msg": "add tests"})

        err = exc_info.value
        assert err.department == "qa"
        assert err.tool_name == "aider_coder"
        assert "aider_coder" not in err.allowed_tools

    def test_engineering_cannot_use_shell_if_not_allowed(self):
        """Engineering without shell in allowlist must be blocked."""
        eng = FakeDepartment("engineering", allowed_tools=["aider_coder"])
        registry = _make_registry(eng)

        with pytest.raises(ToolPermissionError) as exc_info:
            registry.execute("shell", {"command": "rm -rf /"})

        assert exc_info.value.tool_name == "shell"
        assert exc_info.value.department == "engineering"

    def test_permission_error_to_dict_is_complete(self):
        """to_dict() must contain all fields needed for audit logging."""
        err = ToolPermissionError(
            department="product",
            tool_name="pytest",
            allowed_tools=["llm"],
        )
        d = err.to_dict()
        assert d["type"] == "permission_violation"
        assert d["department"] == "product"
        assert d["tool_name"] == "pytest"
        assert "pytest" not in d["allowed_tools"]
        assert "message" in d

    def test_unknown_tool_raises_value_error_not_permission_error(self):
        """Asking for a tool that doesn't exist at all is a ValueError, not a permission error."""
        qa = FakeDepartment("qa", allowed_tools=["shell", "pytest"])
        registry = _make_registry(qa)

        with pytest.raises(ValueError, match="Unknown tool"):
            registry.execute("nonexistent_tool", {})

    # ---------------------------------------------------------------------------
    # Permitted tool tests
    # ---------------------------------------------------------------------------

    def test_qa_can_use_shell(self):
        qa = FakeDepartment("qa", allowed_tools=["shell", "pytest", "linter"])
        registry = _make_registry(qa)
        result = registry.execute("shell", {"command": "echo hi"})
        assert result == "ran: echo hi"

    def test_qa_can_use_pytest(self):
        qa = FakeDepartment("qa", allowed_tools=["shell", "pytest", "linter"])
        registry = _make_registry(qa)
        result = registry.execute("pytest", {"files": "tests/"})
        assert result == "pytest: tests/"

    def test_engineering_can_use_aider_coder(self):
        eng = FakeDepartment("engineering", allowed_tools=["aider_coder", "shell"])
        registry = _make_registry(eng)
        result = registry.execute("aider_coder", {"msg": "refactor parser"})
        assert result == "coded: refactor parser"

    def test_no_department_allows_all_tools(self):
        """Registry with no department attached must not block any tool."""
        registry = _make_registry(FakeDepartment("none", []))
        registry.set_department(None)  # explicitly clear
        # Should not raise
        result = registry.execute("aider_coder", {"msg": "test"})
        assert result == "coded: test"

    def test_set_department_changes_enforcement(self):
        """set_department() must swap enforcement context mid-lifecycle."""
        registry = _make_registry(FakeDepartment("qa", ["shell", "pytest"]))

        # QA can't use aider_coder
        with pytest.raises(ToolPermissionError):
            registry.execute("aider_coder", {})

        # Swap to engineering
        registry.set_department(FakeDepartment("engineering", ["aider_coder"]))
        result = registry.execute("aider_coder", {})
        assert result == "coded: "
