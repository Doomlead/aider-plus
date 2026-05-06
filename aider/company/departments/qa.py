from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable
from aider.run_cmd import run_cmd


class QADepartment(Department):
    name = "qa"
    allowed_tools = ["shell", "pytest", "linter"]

    def get_context_requirements(self) -> list[str]:
        return ["playbook.*", "project.name", "project.phase", "project.prd"]

    async def process(self, task: CompanyTask) -> Deliverable:
        engineering_output = (
            task.payload.get("engineering_result", {})
            if isinstance(task.payload, dict)
            else {}
        )
        engineering_metadata = (
            task.payload.get("engineering_metadata", {})
            if isinstance(task.payload, dict)
            else {}
        )
        if isinstance(engineering_output, dict):
            files_changed = engineering_output.get("metadata", {}).get("files", [])
        else:
            files_changed = engineering_metadata.get("files", [])
        files = list(files_changed or [])

        prd_content = ""
        if isinstance(task.payload, dict):
            prd_content = task.payload.get("prd_content", "")

        test_files = [f for f in files if self._is_test_file(f)]
        if test_files:
            quoted_files = " ".join(shlex.quote(f) for f in test_files)
            test_results = await self._run_shell(f"pytest {quoted_files} -v --tb=short")
            test_passed = "failed" not in test_results.lower()
        else:
            test_results = "No test files found. Manual verification required."
            test_passed = None

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="test_report",
            payload={
                "summary": "QA test report for release approval.",
                "test_results": test_results,
                "test_passed": test_passed,
                "files_covered": files,
                "files_changed": files,
                "prd_excerpt": str(prd_content)[:1000],
                "recommended_checks": self._recommended_checks(test_files),
            },
            status="failure" if test_passed is False else "success",
            metadata={
                "handoff_to": "ceo",
                "blocking": True,
                "gate_name": "release_approval",
                "test_coverage": "executed" if test_files else "manual_required",
                "test_executed": True,
                "context": dict(task.context),
            },
        )

    async def _run_shell(self, command: str) -> str:
        if not self.can_use_tool("shell"):
            return "Permission violation: QA is not allowed to use shell."

        cwd = None
        root = getattr(self.memory, "repo_path", None)
        if root:
            cwd = str(Path(root))

        returncode, output = await asyncio.to_thread(run_cmd, command, False, None, cwd)
        prefix = f"$ {command}\nexit_code={returncode}\n"
        return prefix + output

    @staticmethod
    def _is_test_file(path: str) -> bool:
        name = Path(path).name
        return (
            (name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py")
            or name.endswith(".spec.py")
        )

    @staticmethod
    def _recommended_checks(test_files: list[str]) -> list[str]:
        checks = [
            "Run linting and formatting checks",
            "Run type checks where configured",
            "Perform targeted manual verification for the PRD acceptance criteria",
        ]
        if not test_files:
            checks.insert(0, "Run the project test suite")
        return checks
