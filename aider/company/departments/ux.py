"""
UXDepartment — LLM-powered UX Designer.

Consumes the structured PRD from Product and produces a DesignSpecV2 for
Engineering. DesignSpecV2 locks down screens, components, data contracts,
interaction states, accessibility requirements, and global state details.

_extract_prd() reads prd_structured from task.context (injected by orchestrator
_handoff_task) before falling back to task.payload.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.department import Department
from aider.company.interfaces import Deliverable
from aider.company.schemas import CompanyTask
from aider.company.schemas.design_spec import DesignSpecV2
from aider.company.validators.schema_gate import SchemaGateValidator
from aider.memory import ConversationMemory, ProjectMemory


_UX_SYSTEM_PROMPT = """\
You are an expert UX Designer and product thinker.
Given a PRD, produce a clear, actionable DesignSpecV2 for Engineering.

Return ONLY a valid JSON object — no markdown fences, no preamble — using
exactly these keys:
{
  "title": "<feature name matching the PRD title>",
  "overview": "<1-2 sentence summary of what is being built>",
  "screens": [
    {
      "name": "<ScreenName>",
      "route": "</optional-route-or-null>",
      "description": "<purpose of this screen>",
      "components_used": ["<ComponentSpec name rendered here>", ...],
      "data_fetching": "<how/when data is fetched, or null>"
    }
  ],
  "components": [
    {
      "name": "<PascalCaseComponentName>",
      "description": "<what this component does>",
      "props": [
        {
          "field_name": "<prop/data field name>",
          "data_type": "<string|boolean|Array<User>|number|etc>",
          "source": "<api|local_state|global_state|url_param|prop|event>",
          "description": "<brief explanation>",
          "validation_rules": ["<required|min: 0|max_length: 255|etc>"]
        }
      ],
      "interaction_states": [
        {
          "state_name": "default",
          "trigger": "<initial/normal trigger>",
          "ui_change": "<what user sees>"
        },
        {"state_name": "loading", "trigger": "<pending trigger>", "ui_change": "<loading UI>"},
        {"state_name": "error", "trigger": "<failure trigger>", "ui_change": "<error UI>"}
      ],
      "accessibility_notes": "<aria labels, tab order, screen reader behavior>"
    }
  ],
  "global_state_management": "<how state is managed globally>",
  "accessibility_checklist": {
    "keyboard_navigation": true,
    "screen_reader_labels": true,
    "color_contrast_aa": true,
    "aria_attributes": true,
    "focus_management": true,
    "notes": "<specific WCAG/accessibility notes>"
  },
  "error_boundaries": "<how unexpected errors are caught and displayed>"
}

Rules:
- screens must include at least one entry.
- components must include at least one entry.
- Every screen.components_used value must exactly match a component name.
- Every component must define at least one prop/data contract; do not leave props empty.
- Every component must include default, loading, and error interaction states.
- Use concrete data contracts, routes, API/state sources, and validation rules.
- Do not add keys beyond those listed.
"""

_REVIEW_SYSTEM = """\
You are a senior UX Designer reviewing a DesignSpecV2 before Engineering intake.

Check for these problems only:
1. Missing or empty screens/components.
2. Screen component references that do not exist in components.
3. Components missing props/data contracts.
4. Components missing default, loading, or error interaction states.
5. Generic global_state_management, accessibility_checklist, or error_boundaries.

Return a JSON object only — no markdown, no preamble:
{
  "issues": ["<issue description>", ...],
  "improved_spec": { <full DesignSpecV2 JSON with fixes applied> }
}

If no issues, return: {"issues": [], "improved_spec": null}
"""


class UXDepartment(Department):
    """
    LLM-powered UX Designer department.

    Requires an AiderAgentLoop for LLM calls. Inject at construction time,
    same pattern as EngineeringDepartment and ProductDepartment.
    """

    name = "ux"
    allowed_tools: list[str] = []

    def __init__(
        self,
        project_memory: ProjectMemory,
        agent_loop: AiderAgentLoop,
        conversation_memory: Optional[ConversationMemory] = None,
        config: Optional[DepartmentConfig] = None,
    ):
        super().__init__(project_memory, conversation_memory, config=config)
        self.agent_loop = agent_loop

    def get_context_requirements(self) -> list[str]:
        return ["playbook.*", "project.name", "project.phase", "project.prd"]

    async def process(self, task: CompanyTask) -> Deliverable:
        await self._emit_lifecycle_event("ux_start", {"task_id": task.task_id})

        prd_data = self._extract_prd(task)
        ux_result = await self._generate_design_spec(prd_data, task)
        ux_result = await self._self_review_design(ux_result)

        validator = SchemaGateValidator()
        gate_result = validator.validate(ux_result)

        if not gate_result.approved:
            rejection_payload = gate_result.to_engineering_rejection_payload()
            ux_result = await self._regenerate_with_feedback(prd_data, task, rejection_payload)
            ux_result = await self._self_review_design(ux_result)
            gate_result = validator.validate(ux_result)

        if not gate_result.approved:
            await self._emit_lifecycle_event(
                "ux_validation_failed",
                {"task_id": task.task_id, "errors": gate_result.rejection_reasons},
            )
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type="design_spec",
                payload=gate_result.to_engineering_rejection_payload(),
                status="failed_validation",
                metadata={"validation_errors": gate_result.rejection_reasons},
            )

        parsed_spec = gate_result.parsed_spec
        spec_dict = parsed_spec.model_dump()
        spec_markdown = self._design_spec_to_markdown(parsed_spec)
        context = dict(task.context)
        context["design_spec"] = spec_dict
        context["design_spec_structured"] = spec_dict

        await self._emit_lifecycle_event(
            "ux_complete",
            {
                "title": parsed_spec.title,
                "screens": len(parsed_spec.screens),
                "components": len(parsed_spec.components),
            },
        )

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="design_spec",
            payload=json.dumps(spec_dict, indent=2),
            status="success",
            metadata={
                "handoff_to": "engineering",
                "next_artifact_type": "design_spec",
                "blocking": False,
                "design_spec_structured": spec_dict,
                "design_spec_summary": spec_markdown,
                "ux_self_review_passed": True,
                "design_version": "2.0",
                "context": context,
            },
        )

    async def _generate_design_spec(self, prd_data: dict, task: CompanyTask) -> dict:
        """Generate a DesignSpecV2-compatible dict from the PRD dict or raw payload."""
        result = await self.agent_loop.run_structured(
            task=self._build_generation_task(prd_data, task),
            system_prompt=_UX_SYSTEM_PROMPT,
            enable_caching=self.config.enable_prompt_caching,
            model=self.config.preferred_model or None,
        )
        parsed = self._parse_json(result.get("content", ""))
        if not parsed or "title" not in parsed:
            return self._fallback_design_spec(prd_data, task)
        return parsed

    async def _regenerate_with_feedback(
        self, prd_data: dict, task: CompanyTask, rejection_payload: str
    ) -> dict:
        """Retry generation once with schema-gate feedback injected."""
        result = await self.agent_loop.run_structured(
            task=(
                f"{self._build_generation_task(prd_data, task)}\n\n"
                f"Engineering intake feedback to fix:\n{rejection_payload}"
            ),
            system_prompt=_UX_SYSTEM_PROMPT,
            enable_caching=self.config.enable_prompt_caching,
            model=self.config.preferred_model or None,
        )
        parsed = self._parse_json(result.get("content", ""))
        if not parsed or "title" not in parsed:
            return self._fallback_design_spec(prd_data, task)
        return parsed

    async def _self_review_design(self, spec: dict) -> dict:
        """Light completeness review before the hard schema-gate validation."""
        result = await self.agent_loop.run_structured(
            task=json.dumps(spec, indent=2),
            system_prompt=_REVIEW_SYSTEM,
            enable_caching=self.config.enable_prompt_caching,
        )
        parsed = self._parse_json(result.get("content", ""))
        if not parsed:
            return spec
        improved_raw = parsed.get("improved_spec")
        if improved_raw and isinstance(improved_raw, dict) and "title" in improved_raw:
            return improved_raw
        return spec

    def _extract_prd(self, task: CompanyTask) -> dict:
        """
        Pull structured PRD dict from context (preferred) or payload.

        Context key "prd_structured" is set by orchestrator._handoff_task
        when Product hands off to UX. Fall back to payload for direct calls.
        """
        if isinstance(task.context, dict):
            prd = task.context.get("prd_structured")
            if isinstance(prd, dict) and prd:
                return prd
        if isinstance(task.payload, dict):
            prd = task.payload.get("prd_structured")
            if isinstance(prd, dict) and prd:
                return prd
            prd_content = task.payload.get("prd_content")
            if prd_content:
                return {"title": "Feature", "problem_statement": str(prd_content)}
        return {}

    def _build_generation_task(self, prd_data: dict, task: CompanyTask) -> str:
        if prd_data:
            context_text = json.dumps(prd_data, indent=2)
        elif isinstance(task.payload, str):
            context_text = task.payload
        else:
            context_text = json.dumps(task.payload, indent=2, default=str)

        feedback = ""
        if isinstance(task.payload, dict) and task.payload.get("gate_rejection"):
            feedback = (
                "\n\nEngineering schema-gate feedback to address before returning:\n"
                f"{task.payload['gate_rejection']}"
            )
        return f"PRD to DesignSpecV2:\n{context_text}{feedback}"

    def _fallback_design_spec(self, prd_data: dict, task: CompanyTask) -> dict:
        title = str(prd_data.get("title") or "Feature")
        problem = str(
            prd_data.get("overview")
            or prd_data.get("problem_statement")
            or task.payload
            or "Design specification to be refined with the team."
        )
        return {
            "title": title,
            "overview": problem[:240],
            "screens": [
                {
                    "name": "PrimaryFeatureScreen",
                    "route": None,
                    "description": "Primary screen for completing the requested feature workflow.",
                    "components_used": ["PrimaryFeaturePanel"],
                    "data_fetching": (
                        "Fetch required feature data on mount from the configured "
                        "application API."
                    ),
                }
            ],
            "components": [
                {
                    "name": "PrimaryFeaturePanel",
                    "description": (
                        "Main component that presents the feature workflow and user "
                        "actions."
                    ),
                    "props": [
                        {
                            "field_name": "initialData",
                            "data_type": "object",
                            "source": "prop",
                            "description": "Initial data required to render the feature panel.",
                            "validation_rules": ["required"],
                        }
                    ],
                    "interaction_states": [
                        {
                            "state_name": "default",
                            "trigger": "Feature data is available and no action is in progress.",
                            "ui_change": (
                                "Display the primary feature content and available "
                                "actions."
                            ),
                        },
                        {
                            "state_name": "loading",
                            "trigger": "Feature data or action result is being fetched.",
                            "ui_change": (
                                "Show an accessible loading indicator and disable "
                                "duplicate actions."
                            ),
                        },
                        {
                            "state_name": "error",
                            "trigger": "Feature data fetch or action fails.",
                            "ui_change": "Display an inline error message with a retry affordance.",
                        },
                    ],
                    "accessibility_notes": (
                        "Provide keyboard reachable actions, visible focus, and "
                        "aria-live error messaging."
                    ),
                }
            ],
            "global_state_management": (
                "Use existing application state management for authenticated "
                "user/session data; keep feature-only UI state local."
            ),
            "accessibility_checklist": {
                "keyboard_navigation": True,
                "screen_reader_labels": True,
                "color_contrast_aa": True,
                "aria_attributes": True,
                "focus_management": True,
                "notes": (
                    "Meet WCAG AA contrast, expose loading/error states to "
                    "assistive tech, and preserve logical tab order."
                ),
            },
            "error_boundaries": (
                "Wrap the feature screen in the existing app error boundary and "
                "render a retry-safe fallback message."
            ),
        }

    @staticmethod
    def _design_spec_to_markdown(spec: DesignSpecV2) -> str:
        def _bullet(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "- None"

        screens = _bullet(
            [
                f"{screen.name} ({screen.route or 'no route'}): {screen.description}"
                for screen in spec.screens
            ]
        )
        components = _bullet(
            [
                (
                    f"{component.name}: {component.description}; states="
                    f"{', '.join(state.state_name for state in component.interaction_states)}"
                )
                for component in spec.components
            ]
        )
        a11y = spec.accessibility_checklist.model_dump()
        return (
            f"# Design Spec: {spec.title}\n\n"
            f"## Overview\n{spec.overview}\n\n"
            f"## Screens\n{screens}\n\n"
            f"## Components\n{components}\n\n"
            f"## Global State Management\n{spec.global_state_management}\n\n"
            f"## Accessibility Checklist\n{json.dumps(a11y, indent=2)}\n\n"
            f"## Error Boundaries\n{spec.error_boundaries or 'None specified'}\n"
        )

    @staticmethod
    def _parse_json(content: str) -> dict:
        """
        Extract a JSON object from LLM output.

        Tries strict parse, strips markdown fences, then extracts first {...} block.
        Returns {} on all failures.
        """
        text = content.strip()
        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, ValueError):
            pass
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
        try:
            result = json.loads(text.strip())
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, ValueError):
            pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                return result if isinstance(result, dict) else {}
            except (json.JSONDecodeError, ValueError):
                pass
        return {}
