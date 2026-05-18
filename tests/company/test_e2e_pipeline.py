"""
End-to-end smoke test: company create path -> PRD -> engineering -> commit.

Exercises the real CompanyOrchestrator wiring with real Product and Engineering
department routing logic. LLM calls and Engineering agent execution are stubbed.
Approval gates are auto-resolved. A real temp git repo is used so file writes
and git history can be asserted without external services or API keys.

Run with:
    python -m pytest tests/company/test_e2e_pipeline.py -q
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from aider.agent.loop import AiderAgentLoop
from aider.company.approval import ApprovalManager
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.project import Project
from aider.company.schemas import ApprovalDecision, CompanyTask
from aider.memory import ProjectMemory


# ---------------------------------------------------------------------------
# Fake PRD that ProductDepartment will "generate" via the stubbed LLM
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# DummyCoder — minimal surface required by AiderAgentLoop.__init__
# ---------------------------------------------------------------------------

class DummyCoder:
    """
    Provides the attributes AiderAgentLoop reads on self.coder without
    importing or constructing a real Aider Coder (which requires a real repo
    with git history, a real model, etc.).
    """

    def __init__(self, root: Path):
        self.root = str(root)
        # repo stub used by _build_repo_context
        self.repo = SimpleNamespace(
            root=str(root),
            get_tracked_files=lambda: ["README.md"],
            repo=SimpleNamespace(git=SimpleNamespace(status=lambda *a, **kw: "")),
        )
        # main_model stub used by default_model and _call_llm
        self.main_model = SimpleNamespace(
            name="fake-model",
            extra_params={},
        )
        # conversation / project memory (agent loop checks for these)
        self.done_messages: list = []
        self.conversation_memory = None
        self.project_memory = None
        self.main_system = ""

    def clone(self, **kwargs):
        """
        AiderAgentLoop calls coder.clone() in _build_editor_coder and
        _build_architect_coder.  Return self so we stay dependency-free.
        """
        return self


# ---------------------------------------------------------------------------
# Stubbed LLM responses — keyed on system_prompt substring
# ---------------------------------------------------------------------------

async def fake_run_structured(self, *, task: str, system_prompt: str, **kwargs):
    """
    Intercepts AiderAgentLoop.run_structured so ProductDepartment's LLM calls
    return deterministic JSON without hitting any API.

    Matches each call by a distinctive substring in the system prompt.
    Raises AssertionError on unknown calls so regressions are visible.
    """
    # ProductDepartment: clarification check
    if "needs_clarification" in system_prompt:
        return {"content": json.dumps({"needs_clarification": False, "reason": "clear"})}

    # ProductDepartment: PRD generation
    if "expert Product Manager" in system_prompt:
        return {"content": json.dumps(FAKE_PRD)}

    # ProductDepartment: PRD self-review
    if "senior Product Manager reviewing a PRD" in system_prompt:
        return {"content": json.dumps({"issues": [], "improved_prd": None})}

    # EngineeringDepartment or ReviewerDepartment: code review gate
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

    raise AssertionError(
        f"Unexpected run_structured call — system_prompt starts with:\n"
        f"  {system_prompt[:160]!r}\n"
        "Add a matching branch to fake_run_structured."
    )


# ---------------------------------------------------------------------------
# Stubbed Engineering execution — writes a real file and git commit
# ---------------------------------------------------------------------------

async def fake_engineering_run(self, user_message: str, *, enable_caching=None):
    """
    Replaces AiderAgentLoop.run so EngineeringDepartment produces a real
    file write and git commit without any LLM call or Aider coder execution.

    The return shape must match what EngineeringDepartment reads from the
    agent loop result (summary, files_changed, commits, diffs).
    """
    root = Path(self.coder.root)
    target = root / "main.py"
    target.write_text('print("Hello, world!")\n', encoding="utf-8")

    subprocess.run(["git", "add", "main.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add hello world"],
        cwd=root,
        check=True,
        capture_output=True,
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
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def git_repo(tmp_path):
    """
    Creates a minimal real git repository with one initial commit so the
    pipeline can verify actual file writes and git log depth.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True
    )
    readme = tmp_path / "README.md"
    readme.write_text("# Test project\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_orchestrator(git_repo: Path) -> CompanyOrchestrator:
    """
    Construct an orchestrator with a real ProjectMemory and register only
    Product and Engineering so the pipeline stops at QA (no QA dept = no
    further routing after engineering_result is set).
    """
    from aider.company.departments.engineering import EngineeringDepartment
    from aider.company.departments.product import ProductDepartment

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
    return orchestrator


async def _run_pipeline(git_repo: Path, timeout: float = 10.0) -> CompanyOrchestrator:
    """
    Start department run_loops, submit the initial CEO task, wait until
    engineering_result is populated or the timeout fires, then shut down.
    """
    orchestrator = _build_orchestrator(git_repo)

    loop_tasks = [
        asyncio.create_task(dept.run_loop(), name=f"{name}-loop")
        for name, dept in orchestrator.departments.items()
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

        deadline = asyncio.get_running_loop().time() + timeout
        while orchestrator.active_project.engineering_result is None:
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(
                    "Pipeline did not reach engineering_result within "
                    f"{timeout}s. Project phase: {orchestrator.active_project.phase!r}. "
                    "Check that fake_run_structured handles all ProductDepartment "
                    "system prompts."
                )
            await asyncio.sleep(0.01)

    finally:
        await orchestrator.shutdown()
        for t in loop_tasks:
            t.cancel()
        await asyncio.gather(*loop_tasks, return_exceptions=True)

    return orchestrator


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

def test_company_pipeline_e2e(git_repo):
    """
    Smoke test: a company request flows Product -> (optional approval) ->
    Engineering -> real git commit.

    What is stubbed:
      - AiderAgentLoop.run_structured  (ProductDepartment LLM calls)
      - AiderAgentLoop.run             (EngineeringDepartment agent execution)
      - ApprovalManager.create_request (any blocking gate auto-approves)

    What is real:
      - CompanyOrchestrator routing and project state machine
      - ProductDepartment and EngineeringDepartment process() logic
      - ProjectMemory persistence path
      - git repo — actual file write and commit are verified
    """
    with (
        patch.object(AiderAgentLoop, "run_structured", new=fake_run_structured),
        patch.object(AiderAgentLoop, "run", new=fake_engineering_run),
        patch.object(
            ApprovalManager,
            "create_request",
            new=AsyncMock(
                return_value=ApprovalDecision(
                    approved=True, metadata={"approved_by": "test"}
                )
            ),
        ),
    ):
        orchestrator = asyncio.run(_run_pipeline(git_repo))

    project = orchestrator.active_project

    # ---- PRD was created ------------------------------------------------
    assert project is not None, "active_project must not be None"
    assert project.prd is not None, "project.prd must be populated after Product runs"
    assert "Hello World CLI" in project.prd, (
        f"Expected PRD title in project.prd, got: {project.prd[:200]!r}"
    )

    # ---- Engineering produced a successful result -----------------------
    assert project.engineering_result is not None, (
        "project.engineering_result must be set after Engineering runs"
    )
    assert project.engineering_result.status == "success", (
        f"Expected status='success', got {project.engineering_result.status!r}"
    )

    # ---- Real file was written ------------------------------------------
    main_py = git_repo / "main.py"
    assert main_py.exists(), "main.py must exist after Engineering commits"
    assert main_py.read_text(encoding="utf-8") == 'print("Hello, world!")\n'

    # ---- Real git commit was made ---------------------------------------
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().splitlines()
    assert len(log) >= 2, (
        f"Expected at least 2 commits (init + feat), found: {log}"
    )
    assert "feat: add hello world" in log[0], (
        f"Expected engineering commit at HEAD, found: {log[0]!r}"
    )

    # ---- Project advanced past development ------------------------------
    # With no QA department registered, the orchestrator leaves the project
    # in 'qa' phase (lifecycle transition after engineering success) but does
    # not route further. 'release_ready' is also acceptable if lifecycle
    # skips QA when no qa dept is present.
    assert project.phase in ("qa", "release_ready", "development"), (
        f"Unexpected project phase after pipeline: {project.phase!r}"
    )
