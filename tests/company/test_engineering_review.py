import asyncio
import json

from aider.company.departments.engineering import EngineeringDepartment
from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyTask, Deliverable
from aider.memory import ConversationMemory, ProjectMemory


class FakeToolRegistry:
    def set_department(self, department):
        self.department = department


class FakeAgentLoop:
    def __init__(self, results):
        self.results = list(results)
        self.tool_registry = FakeToolRegistry()
        self.callback_events = []
        self.coder = type(
            "Coder",
            (),
            {"conversation_memory": ConversationMemory(), "repo": None, "root": None},
        )()

    async def run(self, task_text):
        self.last_task_text = task_text
        return self.results.pop(0)

    async def _emit(self, event_name, payload):
        self.callback_events.append((event_name, payload))


class ReviewingEngineeringDepartment(EngineeringDepartment):
    def __init__(self, *, review_passes, **kwargs):
        super().__init__(**kwargs)
        self.review_passes = list(review_passes)
        self.review_feedback_seen = []

    async def _run_reviewer_phase(self, previous_deliverable):
        passed = self.review_passes.pop(0)
        feedback = {
            "overall_assessment": "Approved for QA." if passed else "Needs revision before QA.",
            "issues": (
                []
                if passed
                else [
                    {
                        "file": "app.py",
                        "line_range": "12-14",
                        "severity": "high",
                        "description": "Add a missing assertion.",
                        "suggestion": "Update the implementation.",
                    }
                ]
            ),
            "needs_revision": not passed,
        }
        self.review_feedback_seen.append(feedback)
        metadata = dict(previous_deliverable.metadata)
        metadata.update(
            {
                "review_feedback": feedback,
                "review_passed": passed,
                "issues": feedback["issues"],
                "overall_assessment": feedback["overall_assessment"],
                "needs_revision": feedback["needs_revision"],
            }
        )
        return Deliverable(
            task_id=previous_deliverable.task_id,
            department=self.name,
            artifact_type="code",
            payload=previous_deliverable.payload,
            status="success" if passed else "needs_revision",
            metadata=metadata,
            review_feedback=feedback,
            review_passed=passed,
        )


def test_engineering_loops_back_to_programmer_when_review_needs_revision(tmp_path):
    async def run_test():
        memory = ProjectMemory(str(tmp_path))
        loop = FakeAgentLoop(
            [
                {"summary": "first pass", "metadata": {"files": ["app.py"]}},
                {"summary": "second pass", "metadata": {"files": ["app.py"]}},
            ]
        )
        department = ReviewingEngineeringDepartment(
            project_memory=memory,
            agent_loop=loop,
            conversation_memory=loop.coder.conversation_memory,
            review_passes=[False, True],
        )
        emitted = []

        async def on_event(message):
            emitted.append(message)

        department._on_event = on_event
        task = CompanyTask(
            task_id="task-1",
            origin="product",
            target="engineering",
            artifact_type="prd",
            payload={"prd_content": "Build the feature"},
            context={"playbook_guidance": ["Keep changes tested"]},
        )

        deliverable = await department.process(task)

        assert deliverable.status == "success"
        assert deliverable.review_passed is True
        assert loop.last_task_text.startswith("Original Request / PRD:")
        assert "Build the feature" in loop.last_task_text
        assert "Design Specification:" in loop.last_task_text
        assert "[HIGH] app.py:12-14: Add a missing assertion." in loop.last_task_text
        assert "→ Update the implementation." in loop.last_task_text
        assert "Task:" in loop.last_task_text
        assert (
            "Implement this feature following the PRD and design spec above."
        ) in loop.last_task_text
        assert (
            "Address ALL reviewer feedback from the previous round if present."
        ) in loop.last_task_text
        assert deliverable.metadata["revision_count"] == 2
        assert deliverable.metadata["last_reviewer_issues"] == (
            "1 issues found. Needs revision before QA."
        )
        assert [message.payload["name"] for message in emitted] == [
            "engineering_programmer_start",
            "programmer_complete",
            "engineering_reviewer_start",
            "engineering_revision_needed",
            "engineering_programmer_start",
            "programmer_revision_start",
            "programmer_complete",
            "engineering_reviewer_start",
            "engineering_review_approved",
        ]
        assert emitted[5].payload["revision_count"] == 2
        assert emitted[5].payload["last_reviewer_issues_count"] == 1
        assert emitted[5].payload["last_reviewer_issues"] == (
            "1 issues found. Needs revision before QA."
        )
        assert emitted[5].payload["has_previous_feedback"] is True
        assert [event[0] for event in loop.callback_events] == [
            "engineering_programmer_start",
            "programmer_complete",
            "engineering_reviewer_start",
            "engineering_revision_needed",
            "engineering_programmer_start",
            "programmer_revision_start",
            "programmer_complete",
            "engineering_reviewer_start",
            "engineering_review_approved",
        ]

    asyncio.run(run_test())


class StructuredReviewAgentLoop(FakeAgentLoop):
    def __init__(self, structured_result):
        super().__init__([])
        self.structured_result = structured_result
        self.structured_calls = []

    async def run_structured(self, *, task, system_prompt, model):
        self.structured_calls.append({"task": task, "system_prompt": system_prompt, "model": model})
        return self.structured_result


def test_reviewer_phase_uses_structured_agent_feedback(tmp_path):
    async def run_test():
        memory = ProjectMemory(str(tmp_path))
        loop = StructuredReviewAgentLoop(
            {
                "content": json.dumps(
                    {
                        "review_passed": False,
                        "issues": [
                            {
                                "file": "app.py",
                                "line_range": "10-12",
                                "severity": "high",
                                "description": "Missing bounds check",
                                "suggestion": "Validate empty input before indexing.",
                            }
                        ],
                        "overall_assessment": "Implementation needs a bounds check before QA.",
                        "needs_revision": True,
                    }
                )
            }
        )
        department = EngineeringDepartment(
            project_memory=memory,
            agent_loop=loop,
            conversation_memory=loop.coder.conversation_memory,
        )
        emitted = []

        async def on_event(message):
            emitted.append(message)

        department._on_event = on_event
        department._active_task = CompanyTask(
            task_id="task-2",
            origin="product",
            target="engineering",
            artifact_type="prd",
            payload={"prd_content": "Handle empty inputs safely"},
            context={"playbook_guidance": ["Prefer explicit validation"]},
        )
        previous = Deliverable(
            task_id="task-2",
            department="engineering",
            artifact_type="code",
            payload="implemented",
            status="success",
            metadata={"files": ["app.py"], "diffs": ["diff --git a/app.py b/app.py"]},
        )

        review = await department._run_reviewer_phase(previous)

        assert review.status == "needs_revision"
        assert review.review_passed is False
        assert review.metadata["needs_revision"] is True
        assert review.metadata["issues"][0]["file"] == "app.py"
        assert review.metadata["reviewer_feedback_summary"] == (
            "Implementation needs a bounds check before QA."
        )
        assert loop.structured_calls[0]["model"] == "claude-3-7-sonnet-20250219"
        assert "Original PRD / Requirements" in loop.structured_calls[0]["system_prompt"]
        assert "Handle empty inputs safely" in loop.structured_calls[0]["system_prompt"]
        assert emitted[0].payload["name"] == "reviewer_complete"
        assert emitted[0].payload["reviewer_feedback_summary"] == (
            "Implementation needs a bounds check before QA."
        )

    asyncio.run(run_test())


def test_reviewer_phase_resolves_active_task_for_design_context(tmp_path):
    class TaskRepository:
        def __init__(self, task):
            self.task = task

        async def get(self, task_id):
            assert task_id == self.task.task_id
            return self.task

    async def run_test():
        memory = ProjectMemory(str(tmp_path))
        loop = StructuredReviewAgentLoop(
            {
                "content": json.dumps(
                    {
                        "review_passed": True,
                        "issues": [],
                        "overall_assessment": "Implementation matches the design spec.",
                        "needs_revision": False,
                    }
                )
            }
        )
        department = EngineeringDepartment(
            project_memory=memory,
            agent_loop=loop,
            conversation_memory=loop.coder.conversation_memory,
        )
        task = CompanyTask(
            task_id="task-3",
            origin="ux",
            target="engineering",
            artifact_type="design_spec",
            payload={},
            context={
                "prd_summary": "Build safe empty-state handling",
                "design_spec_structured": {
                    "title": "Empty State",
                    "overview": "Show clear fallback UI.",
                    "key_screens": ["Results"],
                },
            },
        )
        department.task_repository = TaskRepository(task)

        async def no_checks(changed_files):
            return []

        department._run_targeted_checks = no_checks
        previous = Deliverable(
            task_id="task-3",
            department="engineering",
            artifact_type="code",
            payload="implemented",
            status="success",
            metadata={"files": ["app.py"]},
        )

        review = await department._run_reviewer_phase(previous)

        assert review.status == "success"
        assert department._active_task is task
        system_prompt = loop.structured_calls[0]["system_prompt"]
        assert "Build safe empty-state handling" in system_prompt
        assert "Title: Empty State" in system_prompt
        assert "Key Screens: Results" in system_prompt

    asyncio.run(run_test())


def test_parse_reviewer_output_handles_malformed_json_as_blocking_issue(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    loop = FakeAgentLoop([])
    department = EngineeringDepartment(
        project_memory=memory,
        agent_loop=loop,
        conversation_memory=loop.coder.conversation_memory,
    )

    parsed = department._parse_reviewer_output("```json\n{bad json,}\n```")

    assert parsed["review_passed"] is False
    assert parsed["needs_revision"] is True
    assert parsed["issues"][0]["description"] == "Reviewer output was malformed"


def test_implementation_diff_prefers_summary_and_truncates(tmp_path):
    async def run_test():
        memory = ProjectMemory(str(tmp_path))
        loop = FakeAgentLoop([])
        department = EngineeringDepartment(
            project_memory=memory,
            agent_loop=loop,
            conversation_memory=loop.coder.conversation_memory,
        )
        diff = "\n".join(f"line {i}" for i in range(501))

        result = await department._implementation_diff({"diffs_summary": diff})

        assert "line 499" in result
        assert "line 500" not in result
        assert "[TRUNCATED: 1 more lines" in result

    asyncio.run(run_test())


def test_reviewer_metrics_and_playbook_learning(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    loop = FakeAgentLoop([])
    department = EngineeringDepartment(
        project_memory=memory,
        agent_loop=loop,
        conversation_memory=loop.coder.conversation_memory,
    )
    review_data = {
        "review_passed": False,
        "issues": [
            {
                "description": "Missing tests for the new behavior",
                "suggestion": "Add assertions",
            }
        ],
    }

    for _ in range(3):
        department._record_reviewer_metrics(review_data)

    stats = memory.data["observability"]["reviewer_stats"]
    assert stats["total_reviews"] == 3
    assert stats["approval_rate"] == 0.0
    assert stats["avg_issues_per_review"] == 1.0
    assert stats["most_common_issues"] == ["missing_tests"]
    assert any(
        "Always add or update tests" in str(entry)
        for entry in memory.data["playbook"]["coding_standards"]
    )


def test_handoff_task_propagates_prd_and_design_context(tmp_path):
    memory = ProjectMemory(str(tmp_path))
    orchestrator = CompanyOrchestrator(memory)
    prd_structured = {
        "title": "Search",
        "problem_statement": "Users need to find records.",
        "acceptance_criteria": ["Search by name"],
    }
    product_deliverable = Deliverable(
        task_id="task-4",
        department="product",
        artifact_type="prd",
        payload="# PRD\nSearch by name",
        status="success",
        metadata={
            "original_request": "Add search",
            "prd_structured": prd_structured,
            "open_questions": [],
        },
    )

    ux_task = orchestrator._handoff_task(product_deliverable, "ux")

    assert ux_task.context["prd_structured"] == prd_structured
    assert ux_task.context["prd_summary"] == "# PRD\nSearch by name"

    design_spec = {
        "title": "Search UX",
        "overview": "Search box with results list.",
        "key_screens": ["Search"],
    }
    ux_deliverable = Deliverable(
        task_id="task-4",
        department="ux",
        artifact_type="design_spec",
        payload="Search UX summary",
        status="success",
        metadata={
            "context": ux_task.context,
            "design_spec_structured": design_spec,
            "self_review": {"approved": True},
        },
    )

    engineering_task = orchestrator._handoff_task(ux_deliverable, "engineering")

    assert engineering_task.context["prd_structured"] == prd_structured
    assert engineering_task.context["prd_summary"] == "# PRD\nSearch by name"
    assert engineering_task.context["design_spec_structured"] == design_spec
    assert engineering_task.context["design_spec"] == design_spec
    assert engineering_task.context["design_spec_summary"] == "Search UX summary"
    assert engineering_task.context["ux_self_review"] == {"approved": True}
