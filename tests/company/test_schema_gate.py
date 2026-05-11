import asyncio

from aider.company.orchestrator import CompanyOrchestrator
from aider.company.schemas import CompanyTask
from aider.company.validators.schema_gate import SchemaGateValidator
from aider.memory import ProjectMemory


def _valid_design_spec():
    return {
        "title": "Search UX",
        "overview": "A typed search experience with loading and error handling.",
        "screens": [
            {
                "name": "SearchScreen",
                "route": "/search",
                "description": "Lets users search and inspect matching records.",
                "components_used": ["SearchPanel"],
                "data_fetching": "On query submit via GET /api/search?q=term.",
            }
        ],
        "components": [
            {
                "name": "SearchPanel",
                "description": "Owns the search input, result list, and status feedback.",
                "props": [
                    {
                        "field_name": "initialQuery",
                        "data_type": "string",
                        "source": "url_param",
                        "description": "Initial query text read from the URL.",
                        "validation_rules": ["max_length: 255"],
                    }
                ],
                "interaction_states": [
                    {
                        "state_name": "default",
                        "trigger": "Search screen has loaded.",
                        "ui_change": "Show an editable search field and recent results.",
                    },
                    {
                        "state_name": "loading",
                        "trigger": "Search request is in progress.",
                        "ui_change": "Show a spinner and disable duplicate submits.",
                    },
                    {
                        "state_name": "error",
                        "trigger": "Search request fails.",
                        "ui_change": "Show an inline retryable error message.",
                    },
                ],
                "accessibility_notes": "Label the input and announce result status changes.",
            }
        ],
        "global_state_management": "Use URL params for query state and local state for status.",
        "accessibility_checklist": {
            "keyboard_navigation": True,
            "screen_reader_labels": True,
            "color_contrast_aa": True,
            "aria_attributes": True,
            "focus_management": True,
        },
        "error_boundaries": "Use the app-level route error boundary for unexpected failures.",
    }


def test_schema_gate_accepts_complete_design_spec_v2():
    result = SchemaGateValidator().validate(_valid_design_spec())

    assert result.approved is True
    assert result.parsed_spec.title == "Search UX"
    assert result.rejection_reasons == []


def test_schema_gate_rejects_missing_component_states_and_unknown_references():
    spec = _valid_design_spec()
    spec["screens"][0]["components_used"] = ["MissingComponent"]
    spec["components"][0]["interaction_states"] = [
        {
            "state_name": "default",
            "trigger": "Screen loaded.",
            "ui_change": "Show content.",
        }
    ]

    result = SchemaGateValidator().validate(spec)

    assert result.approved is False
    assert "missing a 'loading' interaction state" in "\n".join(result.rejection_reasons)
    assert "references component 'MissingComponent'" in "\n".join(result.rejection_reasons)


def test_orchestrator_bounces_invalid_ux_handoff_before_engineering(tmp_path):
    async def run_test():
        memory = ProjectMemory(str(tmp_path))
        orchestrator = CompanyOrchestrator(memory)
        received_tasks = []

        class UXStub:
            name = "ux"

            def get_context_requirements(self):
                return []

            async def receive(self, task):
                received_tasks.append(task)

        class EngineeringStub:
            name = "engineering"

            def get_context_requirements(self):
                return []

            async def receive(self, task):
                received_tasks.append(task)

        orchestrator.register(UXStub())
        orchestrator.register(EngineeringStub())

        await orchestrator.submit(
            CompanyTask(
                task_id="task-schema",
                origin="ux",
                target="engineering",
                artifact_type="design_spec",
                payload={"title": "Too vague"},
            )
        )

        assert len(received_tasks) == 1
        assert received_tasks[0].target == "ux"
        assert received_tasks[0].payload["gate_rejection"].startswith(
            "Engineering Schema Gate REJECTION."
        )

    asyncio.run(run_test())
