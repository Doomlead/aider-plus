from aider.company.context import ContextBuilder
from aider.company.playbook import MAX_ENTRIES_PER_CATEGORY, PlaybookManager
from aider.company.schemas import CompanyTask
from aider.memory.pattern_extractor import AuditPatternExtractor, make_pattern, pattern_text


class StubState:
    def __init__(self, playbook=None):
        self.playbook = playbook or {}
        self.saved = []

    def get_playbook(self):
        return self.playbook

    def save_playbook(self, playbook):
        self.playbook = playbook
        self.saved.append(playbook)


def test_audit_pattern_extractor_groups_typed_patterns():
    records = [
        {
            "event_type": "qa_fail",
            "metadata": {"failed_tests": ["test_login", "test_logout"]},
            "payload_summary": "assertion failed",
            "timestamp": "2026-05-08T00:00:00Z",
        },
        {
            "event_type": "approval_resolved",
            "metadata": {"approved": False, "feedback": "Make the CTA clearer"},
        },
        {
            "event_type": "deployment_failure",
            "metadata": {"error": "missing DATABASE_URL"},
        },
        {
            "event_type": "engineering_revision_needed",
            "metadata": {"last_reviewer_issues": "Add validation"},
        },
        "ignored",
    ]

    patterns = AuditPatternExtractor(records, "Checkout", "proj-1").extract()

    assert [p["pattern_type"] for p in patterns["coding_standards"]] == [
        "qa_failure",
        "eng_revision",
    ]
    assert patterns["ux_preferences"][0]["pattern_type"] == "ceo_rejection"
    assert patterns["deployment_gotchas"][0]["pattern_type"] == "deploy_failure"
    assert patterns["coding_standards"][0]["project_name"] == "Checkout"
    assert "test_login" in pattern_text(patterns["coding_standards"][0])


def test_playbook_manager_deduplicates_and_caps_entries():
    state = StubState(
        {
            "coding_standards": [
                make_pattern(text="Always validate emails", pattern_type="raw")
            ]
        }
    )
    manager = PlaybookManager(state)
    patterns = {
        "coding_standards": [
            make_pattern(text="Always validate emails", pattern_type="qa_failure"),
            *[
                make_pattern(text=f"Unique rule alpha{i} beta{i}", pattern_type="raw")
                for i in range(MAX_ENTRIES_PER_CATEGORY + 2)
            ],
        ]
    }

    manager.merge_patterns(patterns)

    stored = state.playbook["coding_standards"]
    texts = [pattern_text(entry) for entry in stored]
    assert len(stored) == MAX_ENTRIES_PER_CATEGORY
    assert texts.count("Always validate emails") == 0
    assert texts[-1] == f"Unique rule alpha{MAX_ENTRIES_PER_CATEGORY + 1} beta{MAX_ENTRIES_PER_CATEGORY + 1}"


def test_context_builder_uses_ranked_playbook_query_for_structured_entries():
    playbook = {
        "coding_standards": [
            make_pattern(text="Validate Stripe webhook signatures", pattern_type="raw"),
            "Use semantic headings in UI screens",
            make_pattern(text="Test payment retry edge cases", pattern_type="raw"),
            make_pattern(text="Document deployment rollback steps", pattern_type="raw"),
            make_pattern(text="Prefer small pure functions", pattern_type="raw"),
            make_pattern(text="Mock payment gateways in unit tests", pattern_type="raw"),
        ]
    }
    state = StubState(playbook)
    builder = ContextBuilder(state)
    task = CompanyTask(
        task_id="task-payments",
        origin="product",
        target="engineering",
        artifact_type="prd",
        payload={"instruction": "Implement Stripe webhook signatures"},
    )

    requested = builder._requested_playbook(["playbook.coding_standards"], task)

    assert list(requested) == ["coding_standards"]
    assert 0 < len(requested["coding_standards"]) <= 5
    assert requested["coding_standards"][0] == "Validate Stripe webhook signatures"
    assert all(isinstance(item, str) for item in requested["coding_standards"])
