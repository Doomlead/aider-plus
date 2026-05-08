"""
Tests that ToolRegistry enforces department-scoped tool allowlists correctly.

Covers:
- Unknown tool raises ValueError *before* any permission check
- Permitted tools execute successfully
- Non-permitted tools raise ToolPermissionError with correct metadata
- set_department() swaps enforcement context
- Registry with no department allows everything
"""
from __future__ import annotations

import pytest

from aider.agent.tools import Tool, ToolPermissionError, ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeDepartment:
    """Minimal stand-in that mirrors the interface ToolRegistry reads."""

    def __init__(self, name: str, allowed_tools: list[str]):
        self.name = name
        self.allowed_tools = list(allowed_tools)

    def can_use_tool(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools


def make_registry(department: FakeDepartment | None = None) -> ToolRegistry:
    """Return a populated registry bound to *department*."""
    registry = ToolRegistry(department=department)
    registry.register(Tool(
        name="shell",
        description="Run shell commands",
        func=lambda command="": f"ran:{command}",
        parameters={"name": "shell"},
    ))
    registry.register(Tool(
        name="pytest",
        description="Run pytest",
        func=lambda files="": f"pytest:{files}",
        parameters={"name": "pytest"},
    ))
    registry.register(Tool(
        name="aider_coder",
        description="Invoke the Aider coder",
        func=lambda msg="": f"coded:{msg}",
        parameters={"name": "aider_coder"},
    ))
    return registry


# ---------------------------------------------------------------------------
# Unknown tool — must raise ValueError regardless of department
# ---------------------------------------------------------------------------

class TestUnknownTool:

    def test_unknown_tool_raises_value_error(self):
        """Asking for a tool not in the registry is always ValueError."""
        registry = make_registry(FakeDepartment("qa", ["shell", "pytest"]))
        with pytest.raises(ValueError, match="Unknown tool"):
            registry.execute("nonexistent_tool", {})

    def test_unknown_tool_raises_value_error_not_permission_error(self):
        """ValueError must not be a ToolPermissionError subclass."""
        registry = make_registry(FakeDepartment("qa", ["shell"]))
        with pytest.raises(ValueError) as exc_info:
            registry.execute("ghost_tool", {})
        assert not isinstance(exc_info.value, ToolPermissionError)

    def test_unknown_tool_check_precedes_auth(self):
        """
        Even when the department would block the call, unknown tools raise
        ValueError (not ToolPermissionError). This confirms the ordering fix:
        existence check happens before _authorize.
        """
        # Department that blocks everything
        dept = FakeDepartment("restrictive", allowed_tools=[])
        registry = make_registry(dept)
        with pytest.raises(ValueError, match="Unknown tool"):
            registry.execute("completely_unknown", {})


# ---------------------------------------------------------------------------
# Permission violation tests
# ---------------------------------------------------------------------------

class TestPermissionEnforcement:

    def test_qa_cannot_use_aider_coder(self):
        """QA department must not invoke the engineering tool."""
        qa = FakeDepartment("qa", allowed_tools=["shell", "pytest", "linter"])
        registry = make_registry(qa)

        with pytest.raises(ToolPermissionError) as exc_info:
            registry.execute("aider_coder", {"msg": "add tests"})

        err = exc_info.value
        assert err.department == "qa"
        assert err.tool_name == "aider_coder"
        assert "aider_coder" not in err.allowed_tools

    def test_engineering_cannot_use_shell_when_not_allowed(self):
        """Engineering without shell in its allowlist must be blocked."""
        eng = FakeDepartment("engineering", allowed_tools=["aider_coder"])
        registry = make_registry(eng)

        with pytest.raises(ToolPermissionError) as exc_info:
            registry.execute("shell", {"command": "echo hi"})

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
        assert "message" in d and d["message"]

    def test_permission_error_is_subclass_of_permission_error(self):
        """ToolPermissionError must be catchable as the stdlib PermissionError."""
        with pytest.raises(PermissionError):
            raise ToolPermissionError(
                department="qa", tool_name="aider_coder", allowed_tools=[]
            )


# ---------------------------------------------------------------------------
# Permitted tool tests
# ---------------------------------------------------------------------------

class TestPermittedTools:

    def test_qa_can_use_shell(self):
        qa = FakeDepartment("qa", allowed_tools=["shell", "pytest", "linter"])
        registry = make_registry(qa)
        assert registry.execute("shell", {"command": "echo"}) == "ran:echo"

    def test_qa_can_use_pytest(self):
        qa = FakeDepartment("qa", allowed_tools=["shell", "pytest", "linter"])
        registry = make_registry(qa)
        assert registry.execute("pytest", {"files": "tests/"}) == "pytest:tests/"

    def test_engineering_can_use_aider_coder(self):
        eng = FakeDepartment("engineering", allowed_tools=["aider_coder", "shell"])
        registry = make_registry(eng)
        assert registry.execute("aider_coder", {"msg": "refactor"}) == "coded:refactor"


# ---------------------------------------------------------------------------
# Department lifecycle tests
# ---------------------------------------------------------------------------

class TestRegistryLifecycle:

    def test_no_department_allows_all_registered_tools(self):
        """Registry with no department must not block any registered tool."""
        registry = make_registry(department=None)
        assert registry.execute("aider_coder", {}) == "coded:"
        assert registry.execute("shell", {}) == "ran:"
        assert registry.execute("pytest", {}) == "pytest:"

    def test_set_department_none_clears_enforcement(self):
        """After set_department(None), all tools are accessible."""
        registry = make_registry(FakeDepartment("qa", ["shell"]))
        with pytest.raises(ToolPermissionError):
            registry.execute("aider_coder", {})

        registry.set_department(None)
        assert registry.execute("aider_coder", {}) == "coded:"

    def test_set_department_swaps_enforcement(self):
        """set_department() must change which tools are permitted mid-lifecycle."""
        registry = make_registry(FakeDepartment("qa", ["shell", "pytest"]))

        with pytest.raises(ToolPermissionError):
            registry.execute("aider_coder", {})

        registry.set_department(FakeDepartment("engineering", ["aider_coder"]))
        assert registry.execute("aider_coder", {}) == "coded:"

        # Old qa permission is gone — shell is now blocked
        with pytest.raises(ToolPermissionError):
            registry.execute("shell", {})
