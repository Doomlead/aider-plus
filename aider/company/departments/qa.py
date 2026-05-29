from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.department import Department
from aider.memory import ConversationMemory, ProjectMemory
from aider.company.schemas import CompanyTask, Deliverable
from aider.run_cmd import run_cmd


class QADepartment(Department):
    name = "qa"
    allowed_tools = ["shell", "pytest", "linter"]

    def __init__(
        self,
        project_memory: ProjectMemory,
        agent_loop: Optional[AiderAgentLoop] = None,
        conversation_memory: Optional[ConversationMemory] = None,
        config: Optional[DepartmentConfig] = None,
    ):
        super().__init__(project_memory, conversation_memory, config=config)
        self.agent_loop = agent_loop

    def get_context_requirements(self) -> list[str]:
        return [
            "playbook.*",
            "skills.shared",
            "skills.qa",
            "project.name",
            "project.phase",
            "project.prd",
            "codegraph.affected",
        ]

    async def process(self, task: CompanyTask) -> Deliverable:
        from aider.company.schemas import QAFeedback  # local import avoids circular

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

        prior_revision = 0
        if isinstance(task.context, dict):
            prior_fb = task.context.get("qa_feedback")
            if isinstance(prior_fb, dict):
                prior_revision = int(prior_fb.get("revision_number", 0))

        test_files = self._targeted_test_files(files, task.context)

        if test_files:
            quoted_files = " ".join(shlex.quote(f) for f in test_files)
            test_results = await self._run_shell(f"pytest {quoted_files} -v --tb=short")
            test_passed = "failed" not in test_results.lower()
            failed_tests = self._parse_failed_tests(test_results)
        else:
            test_results = "No test files found. Manual verification required."
            test_passed = None
            failed_tests = []

        # Build structured feedback object when tests actually failed
        qa_feedback: QAFeedback | None = None
        if test_passed is False:
            qa_feedback = QAFeedback(
                test_passed=False,
                failed_tests=failed_tests,
                failure_output=test_results,
                files_covered=test_files,
                recommended_fixes=self._recommended_fixes_from_failures(
                    failed_tests, test_results
                ),
                revision_number=prior_revision + 1,
                prd_excerpt=str(prd_content)[:500],
            )

        route_aware_plan = self._route_aware_qa_plan(task.context)
        deliverable_metadata = {
            "handoff_to": "engineering" if test_passed is False else "ceo",
            "blocking": test_passed
            is not False,  # only block for release if pass/unknown
            "gate_name": "release_approval",
            "test_coverage": "executed" if test_files else "manual_required",
            "test_executed": True,
            "targeted_by_codegraph": self._codegraph_affected_tests(task.context),
            "codegraph_suggested_commands": self._codegraph_suggested_commands(
                task.context
            ),
            "route_aware_plan": route_aware_plan,
            "context": dict(task.context) if isinstance(task.context, dict) else {},
        }
        if qa_feedback is not None:
            deliverable_metadata["qa_feedback"] = qa_feedback.to_dict()
            deliverable_metadata["handoff_to"] = "engineering"
            deliverable_metadata["blocking"] = False  # don't gate CEO, go straight back

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="test_report",
            payload={
                "summary": (
                    f"QA failed — routing back to Engineering (revision {prior_revision + 1})."
                    if test_passed is False
                    else "QA test report for release approval."
                ),
                "test_results": test_results,
                "test_passed": test_passed,
                "files_covered": files,
                "files_changed": files,
                "prd_excerpt": str(prd_content)[:1000],
                "recommended_checks": self._recommended_checks(
                    test_files, route_aware_plan
                ),
                "targeted_by_codegraph": self._codegraph_affected_tests(task.context),
                "codegraph_suggested_commands": self._codegraph_suggested_commands(
                    task.context
                ),
                "route_aware_plan": route_aware_plan,
                "qa_feedback": qa_feedback.to_dict() if qa_feedback else None,
            },
            status="failure" if test_passed is False else "success",
            metadata=deliverable_metadata,
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

    def _targeted_test_files(
        self, changed_files: list[str], context: dict | None
    ) -> list[str]:
        tests = [f for f in changed_files if self._is_test_file(f)]
        tests.extend(self._codegraph_affected_tests(context))
        return sorted(dict.fromkeys(tests))

    @staticmethod
    def _codegraph_affected_tests(context: dict | None) -> list[str]:
        affected = QADepartment._codegraph_affected_payload(context)
        tests = affected.get("affected_tests") if isinstance(affected, dict) else []
        if isinstance(tests, str):
            return [tests]
        return [str(test) for test in tests or [] if test]

    @staticmethod
    def _is_test_file(path: str) -> bool:
        name = Path(path).name
        return (
            (name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py")
            or name.endswith(".spec.py")
        )

    @staticmethod
    def _recommended_checks(
        test_files: list[str], route_aware_plan: list[dict] | None = None
    ) -> list[str]:
        checks = [
            "Run linting and formatting checks",
            "Run type checks where configured",
            "Perform targeted manual verification for the PRD acceptance criteria",
        ]
        if not test_files:
            checks.insert(0, "Run the project test suite")
        for route_plan in route_aware_plan or []:
            route = route_plan.get("route")
            framework = route_plan.get("framework")
            checks.append(
                f"Verify impacted {framework} route {route} for happy path, auth/error boundaries, and navigation/API contract regressions"
            )
        return checks

    @staticmethod
    def _codegraph_suggested_commands(context: dict | None) -> list[str]:
        affected = QADepartment._codegraph_affected_payload(context)
        commands = (
            affected.get("suggested_commands") if isinstance(affected, dict) else []
        )
        if isinstance(commands, str):
            return [commands]
        return [str(command) for command in commands or [] if command]

    @staticmethod
    def _route_aware_qa_plan(context: dict | None) -> list[dict]:
        routes = QADepartment._codegraph_routes(context)
        plan = []
        for route in routes:
            framework = str(route.get("framework") or "route")
            checks = [
                "Exercise route-level happy path and empty/error states.",
                "Verify auth, permission, validation, and caching boundaries for this route.",
            ]
            checks.extend(QADepartment._framework_route_checks(framework))
            plan.append(
                {
                    "framework": framework,
                    "method": route.get("method"),
                    "route": route.get("route"),
                    "file_path": route.get("file_path"),
                    "checks": checks,
                }
            )
        return plan

    @staticmethod
    def _codegraph_routes(context: dict | None) -> list[dict]:
        if not isinstance(context, dict):
            return []
        codegraph = (
            context.get("codegraph")
            if isinstance(context.get("codegraph"), dict)
            else {}
        )
        route_sources = []
        for key in ("impact", "affected_tests"):
            payload = codegraph.get(key) if isinstance(codegraph, dict) else {}
            if isinstance(payload, dict):
                route_sources.extend(payload.get("routes") or [])
        routes = []
        seen = set()
        for route in route_sources:
            if isinstance(route, dict) and route.get("route"):
                item = dict(route)
            elif route:
                item = {"framework": "route", "method": None, "route": str(route)}
            else:
                continue
            key = (
                item.get("framework"),
                item.get("method"),
                item.get("route"),
                item.get("file_path"),
            )
            if key not in seen:
                seen.add(key)
                routes.append(item)
        return routes

    @staticmethod
    def _framework_route_checks(framework: str) -> list[str]:
        checks_by_framework = {
            "django": [
                "Run URL resolver/view tests for path converters and middleware behavior."
            ],
            "rails": [
                "Run request/system specs covering routing, controller filters, and redirects."
            ],
            "express": [
                "Run HTTP handler tests for middleware ordering and status/body contracts."
            ],
            "javascript": [
                "Run HTTP handler tests for middleware ordering and status/body contracts."
            ],
            "nestjs": [
                "Run controller/e2e specs for guards, pipes, interceptors, and DTO validation."
            ],
            "nextjs": [
                "Run page/route-handler checks for server/client rendering, params, and redirects."
            ],
            "nuxt": [
                "Run page/navigation checks for route params, middleware, and server data loading."
            ],
            "sveltekit": [
                "Run load/action checks for route params, form actions, and navigation states."
            ],
            "python": [
                "Run API route tests for dependency injection, request validation, and response codes."
            ],
        }
        return checks_by_framework.get(
            framework.lower(), ["Run framework-specific route smoke checks."]
        )

    @staticmethod
    def _codegraph_affected_payload(context: dict | None) -> dict:
        if not isinstance(context, dict):
            return {}
        codegraph = (
            context.get("codegraph")
            if isinstance(context.get("codegraph"), dict)
            else {}
        )
        affected = (
            codegraph.get("affected_tests") if isinstance(codegraph, dict) else {}
        )
        return affected if isinstance(affected, dict) else {}

    @staticmethod
    def _parse_failed_tests(pytest_output: str) -> list[str]:
        """
        Extract individual failed test node IDs from pytest --tb=short output.
        Lines look like: 'FAILED tests/test_foo.py::test_bar - AssertionError'
        """
        failed = []
        for line in pytest_output.splitlines():
            stripped = line.strip()
            if stripped.startswith("FAILED "):
                # "FAILED tests/foo.py::test_name - reason" → take the part before ' - '
                node_id = stripped[len("FAILED ") :].split(" - ")[0].strip()
                if node_id:
                    failed.append(node_id)
        return failed

    @staticmethod
    def _recommended_fixes_from_failures(
        failed_tests: list[str], output: str
    ) -> list[str]:
        """
        Derive actionable Engineering recommendations from the failure list and output.
        """
        fixes = []
        if not failed_tests:
            fixes.append("Review the full test output for assertion errors.")
            return fixes

        for node_id in failed_tests[:5]:  # cap at 5 to keep context small
            fixes.append(f"Fix failing test: {node_id}")

        if "ImportError" in output or "ModuleNotFoundError" in output:
            fixes.append("Resolve import/module errors before re-running tests.")
        if "AssertionError" in output:
            fixes.append("Check assertion logic — expected vs actual values differ.")
        if "fixture" in output.lower():
            fixes.append("Review pytest fixture setup/teardown.")

        return fixes
