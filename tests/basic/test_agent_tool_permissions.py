import unittest
from types import SimpleNamespace

from aider.agent.tools import Tool, ToolPermissionError, ToolRegistry
from aider.company.department import Department


class DummyDepartment(Department):
    name = "dummy"
    allowed_tools = ["safe_tool"]

    async def process(self, task):
        raise NotImplementedError


class TestAgentToolPermissions(unittest.TestCase):
    def test_department_can_use_tool_whitelist(self):
        department = DummyDepartment(project_memory=SimpleNamespace())

        self.assertTrue(department.can_use_tool("safe_tool"))
        self.assertFalse(department.can_use_tool("shell"))

    def test_registry_rejects_non_whitelisted_department_tool(self):
        department = DummyDepartment(project_memory=SimpleNamespace())
        registry = ToolRegistry(department=department)
        registry.register(
            Tool(
                name="shell",
                description="Dangerous shell access",
                func=lambda: "ran",
                parameters={"type": "function", "function": {"name": "shell"}},
            )
        )

        with self.assertRaises(ToolPermissionError) as ctx:
            registry.execute("shell", {})

        self.assertEqual(ctx.exception.to_dict()["type"], "permission_violation")
        self.assertEqual(ctx.exception.to_dict()["department"], "dummy")
        self.assertEqual(ctx.exception.to_dict()["tool_name"], "shell")


if __name__ == "__main__":
    unittest.main()
