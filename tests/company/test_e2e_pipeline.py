"""
End-to-end smoke test: company create path -> PRD -> auto-approve -> engineering -> commit.

Uses a real temp git repo but stubs LLM calls and auto-resolves approvals.
No API key required.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aider.agent.loop import AiderAgentLoop
from aider.company.approval import ApprovalManager
from aider.company.departments.engineering import EngineeringDepartment
from aider.company.departments.product import ProductDepartment
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.project import Project
from aider.company.schemas import ApprovalDecision, CompanyTask
from aider.memory import ProjectMemory


FAKE_PRD = {
    "title": "Hello World CLI",
    "problem_statement": "Developers need a tiny CLI smoke target to verify the repo wiring.",
    "goals": ["Print a deterministic hello world message."],
    "success_metrics": ["100% of runs of `python main.py` print the expected line."],
    "user_stories": [
        "As a developer, I want to run a tiny CLI so that I can verify the project works.",
        "As a maintainer, I want a deterministic output so that smoke tests are stable.",
    ],
    "acceptance_criteria": [
        "Given the repo checkout, when `python main.py` runs, then stdout is `Hello, world!`.",
    ],
    "technical_considerations": ["Keep the implementation dependency-free."],
    "out_of_scope": ["Packaging and command-line flags are out of scope for the MVP."],
    "priority": "MVP",
    "open_questions": [],
}


class DummyCoder:
    """Minimal coder surface needed by AiderAgentLoop and EngineeringDepartment."""

    def __init__(self, root: Path):
        self.root = str(root)
        self.repo = SimpleNamespace(root=str(root), get_tracked_files=lambda: ["README.md"])
        self.main_model = SimpleNamespace(name="fake-model", extra_params={})
        self.done_messages = []
        self.conversation_memory = None
        self.project_memory = None
        self.main_system = ""

    def clone(self, **kwargs):
        return self


@pytest.fixture()
def git_repo(tmp_path):
    """Minimal real git repo with one initial commit."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    readme = tmp_path / "README.md"
    readme.write_text("# Test project\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


async def fake_run_structured(self, *, task, system_prompt, **kwargs):
    """Return deterministic structured LLM responses for Product and reviewer calls."""
    if "needs_clarification" in system_prompt:
        return {"content": json.dumps({"needs_clarification": False, "reason": "clear"})}
    if "expert Product Manager" in system_prompt:
        return {"content": json.dumps(FAKE_PRD)}
    if "senior Product Manager reviewing a PRD" in system_prompt:
        return {"content": json.dumps({"issues": [], "improved_prd": None})}
    if "strict code reviewer" in system_prompt:
        return {
            "content": json.dumps(
                {
                    "review_passed": True,
                    "issues": [],
                    "overall_assessment": "Looks good for QA.",
                    "needs_revision": False,
                }
            )
        }
    raise AssertionError(f"Unexpected structured LLM prompt: {system_prompt[:120]}")


async def fake_engineering_run(self, user_message, *, enable_caching=None):
    """Actually write and commit a file while avoiding an LLM/API call."""
    root = Path(self.coder.root)
    target = root / "main.py"
    target.write_text('print("Hello, world!")\n', encoding="utf-8")
    subprocess.run(["git", "add", "main.py"], cwd=root, check=True)
    commit = subprocess.run(
        ["git", "commit", "-m", "feat: add hello world"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    commit_hash = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "summary": "Implemented hello world CLI.",
        "files_changed": ["main.py"],
        "commits": [commit_hash],
        "diffs": ['+print("Hello, world!")'],
        "metadata": {"commit_stdout": commit.stdout},
    }


async def auto_approve(self, task):
    return ApprovalDecision(approved=True, metadata={"approved_by": "test"})


def test_company_pipeline_e2e(git_repo):
    """
    Smoke test: a company request flows Product -> approval -> Engineering -> commit.

    LLM calls are stubbed. Approval is auto-resolved. Git commit is real.
    """

    async def run_pipeline():
        memory = ProjectMemory(str(git_repo))
        coder = DummyCoder(git_repo)
        agent_loop = AiderAgentLoop(coder=coder)
        orchestrator = CompanyOrchestrator(memory)
        orchestrator.active_project = Project(
            project_id="hello-world-cli",
            name="Hello World CLI",
            phase="prototyping",
        )
        orchestrator.register(ProductDepartment(memory, agent_loop))
        orchestrator.register(EngineeringDepartment(memory, agent_loop))

        loops = [
            asyncio.create_task(dept.run_loop())
            for dept in orchestrator.departments.values()
        ]
        try:
            await orchestrator.submit(
                CompanyTask(
                    task_id="task-hello-world",
                    origin="ceo",
                    target="product",
                    artifact_type="raw_prompt",
                    payload="Build a hello world CLI tool.",
                    blocking=False,
                )
            )

            deadline = asyncio.get_running_loop().time() + 5
            while orchestrator.active_project.engineering_result is None:
                if asyncio.get_running_loop().time() > deadline:
                    raise TimeoutError("Company pipeline did not reach Engineering")
                await asyncio.sleep(0.01)

            return orchestrator
        finally:
            await orchestrator.shutdown()
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)

    with (
        patch.object(AiderAgentLoop, "run_structured", new=fake_run_structured),
        patch.object(AiderAgentLoop, "run", new=fake_engineering_run),
        patch.object(ApprovalManager, "create_request", new=auto_approve),
    ):
        orchestrator = asyncio.run(run_pipeline())

    project = orchestrator.active_project
    assert project is not None
    assert project.prd is not None
    assert "Hello World CLI" in project.prd
    assert project.engineering_result is not None
    assert project.engineering_result.status == "success"
    assert (git_repo / "main.py").read_text(encoding="utf-8") == 'print("Hello, world!")\n'

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().splitlines()
    assert len(log) >= 2
    assert "feat: add hello world" in log[0]
    assert project.phase == "qa"
