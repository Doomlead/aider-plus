import asyncio
import json

from aider.company.departments.engineering import EngineeringDepartment
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
            "summary": "Approved for QA." if passed else "Needs revision before QA.",
            "what_is_good": ["diff reviewed"],
            "concerns": [],
            "priority_issues": []
            if passed
            else [
                {
                    "priority": "P1",
                    "issue": "Add a missing assertion.",
                    "action": "Update the implementation.",
                }
            ],
        }
        self.review_feedback_seen.append(feedback)
        metadata = dict(previous_deliverable.metadata)
        metadata.update({"review_feedback": feedback, "review_passed": passed})
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
        assert "Previous Reviewer Feedback (fix these issues)" in loop.last_task_text
        assert [message.payload["name"] for message in emitted] == [
            "engineering_programmer_start",
            "engineering_reviewer_start",
            "engineering_revision_needed",
            "engineering_programmer_start",
            "engineering_reviewer_start",
            "engineering_review_approved",
        ]
        assert [event[0] for event in loop.callback_events] == [
            "engineering_programmer_start",
            "engineering_reviewer_start",
            "engineering_revision_needed",
            "engineering_programmer_start",
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
        self.structured_calls.append(
            {"task": task, "system_prompt": system_prompt, "model": model}
        )
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
                        "overall_assessment": (
                            "Implementation needs a bounds check before QA."
                        ),
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
        assert (
            "Original PRD / Requirements" in loop.structured_calls[0]["system_prompt"]
        )
        assert "Handle empty inputs safely" in loop.structured_calls[0]["system_prompt"]
        assert emitted[0].payload["name"] == "reviewer_complete"
        assert emitted[0].payload["reviewer_feedback_summary"] == (
            "Implementation needs a bounds check before QA."
        )

    asyncio.run(run_test())
