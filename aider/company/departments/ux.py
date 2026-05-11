"""
UXDepartment — LLM-powered UX Designer.

Consumes the structured PRD from Product and produces a DesignSpec for Engineering.
The DesignSpec contains key screens, component recommendations, user flows,
accessibility notes, and visual style direction.

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
from aider.company.validators.schema_gate import SchemaGateValidator
from aider.memory import ConversationMemory, ProjectMemory

_UX_SYSTEM_PROMPT = """\
You are an expert UX Designer and product thinker.
Given a PRD, produce a clear, actionable DesignSpecV2 for Engineering.

Return ONLY a valid JSON object — no markdown fences, no preamble — using
exactly these keys:
{
  "title": "<feature name matching the PRD title>",
  "overview": "<2-3 sentence design philosophy for this feature>",
  "screens": [
    {
      "name": "<screen name>",
      "route": "<route or null>",
      "description": "<screen purpose>",
      "components_used": ["<ComponentName>", ...],
      "data_fetching": "<API/query description or null>"
    }
  ],
  "components": [
    {
      "name": "<ComponentName>",
      "description": "<what it does>",
      "props": [
        {
          "field_name": "<prop/data field>",
          "data_type": "<string, boolean, Array<User>, etc.>",
          "source": "api | local_state | global_state | url_param | prop | event",
          "description": "<field purpose>",
          "validation_rules": ["<rule>", ...]
        }
      ],
      "interaction_states": [
        {"state_name": "loading", "trigger": "<trigger>", "ui_change": "<UI behavior>"},
        {"state_name": "error", "trigger": "<trigger>", "ui_change": "<UI behavior>"}
      ],
      "accessibility_notes": "<component a11y notes or null>"
    }
  ],
  "global_state_management": "<state ownership and synchronization strategy>",
  "accessibility_checklist": {
    "keyboard_navigation": true,
    "screen_reader_labels": true,
    "color_contrast_aa": true,
    "aria_attributes": true,
    "focus_management": true,
    "notes": "<specific WCAG notes>"
  },
  "error_boundaries": "<error handling boundaries or null>"
}

Rules:
- screens must include at minimum the primary action screen and an error/empty state screen.
- components must list at least 3 components.
- Every component must include loading/pending and error/failed interaction states.
- Every screen.components_used entry must match a component.name exactly.
- Accessibility notes must reference keyboard navigation and at least one WCAG criterion.
- Do not add keys beyond those listed.
"""

_REVIEW_SYSTEM = """\
You are a senior UX Designer reviewing a DesignSpecV2 for completeness.

Check for these problems only:
1. fewer than 2 screens
2. fewer than 3 components
3. component entries missing props or interaction states
4. components missing loading/pending or error/failed states
5. screen components_used references that do not match component names
6. accessibility_checklist entries that are false or missing WCAG-specific notes
7. missing global_state_management or error boundaries guidance

Return a JSON object only — no markdown, no preamble:
{
  "issues": ["<issue description>", ...],
  "improved_spec": { <full DesignSpecV2 JSON with fixes applied, same schema as input> }
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

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process(self, task: CompanyTask) -> Deliverable:
        await self._emit_lifecycle_event("ux_start", {"task_id": task.task_id})

        prd_data = self._extract_prd(task)
        raw_spec = await self._generate_design_spec(prd_data, task)
        raw_spec = await self._self_review_design(raw_spec)

        validator = SchemaGateValidator()
        gate = validator.validate(raw_spec)

        if not gate.approved:
            rejection_msg = gate.to_engineering_rejection_payload()
            raw_spec = await self._generate_design_spec(prd_data, task, feedback=rejection_msg)
            raw_spec = await self._self_review_design(raw_spec)
            gate = validator.validate(raw_spec)

        context = dict(task.context)
        if not gate.approved:
            rejection_payload = gate.to_engineering_rejection_payload()
            context["design_spec_validation_errors"] = gate.rejection_reasons
            await self._emit_lifecycle_event(
                "ux_validation_failed",
                {"task_id": task.task_id, "errors": gate.rejection_reasons},
            )
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type="design_spec",
                payload=rejection_payload,
                status="validation_failed",
                metadata={
                    "handoff_to": "ux",
                    "next_artifact_type": "design_spec",
                    "blocking": True,
                    "validation_errors": gate.rejection_reasons,
                    "schema_gate_approved": False,
                    "context": context,
                },
            )

        parsed_spec = gate.parsed_spec
        structured_spec = parsed_spec.model_dump() if parsed_spec else raw_spec
        spec_markdown = parsed_spec.to_markdown() if parsed_spec else json.dumps(raw_spec, indent=2)
        context["design_spec"] = structured_spec
        context["design_spec_structured"] = structured_spec

        await self._emit_lifecycle_event(
            "ux_complete",
            {
                "title": structured_spec.get("title"),
                "screens": len(structured_spec.get("screens", [])),
                "components": len(structured_spec.get("components", [])),
                "schema_gate_approved": True,
            },
        )

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="design_spec",
            payload=spec_markdown,
            status="success",
            metadata={
                "handoff_to": "engineering",
                "next_artifact_type": "design_spec",
                "blocking": False,
                "design_spec_structured": structured_spec,
                "design_spec_summary": structured_spec.get("overview"),
                "design_version": "2.0",
                "ux_self_review_passed": True,
                "schema_gate_approved": True,
                "context": context,
            },
        )

    # ------------------------------------------------------------------
    # Stage handlers
    # ------------------------------------------------------------------

    async def _generate_design_spec(
        self, prd_data: dict, task: CompanyTask, feedback: str | None = None
    ) -> dict:
        """Generate a structured DesignSpecV2 dict from the PRD dict or raw payload."""
        if prd_data:
            context_text = json.dumps(prd_data, indent=2)
        elif isinstance(task.payload, str):
            context_text = task.payload
        else:
            context_text = json.dumps(task.payload, indent=2, default=str)

        if feedback:
            context_text = f"{context_text}\n\nSchema gate feedback to fix:\n{feedback}"

        result = await self.agent_loop.run_structured(
            task=f"PRD to DesignSpecV2:\n{context_text}",
            system_prompt=_UX_SYSTEM_PROMPT,
            enable_caching=self.config.enable_prompt_caching,
            model=self.config.preferred_model or None,
        )
        parsed = self._parse_json(result.get("content", ""))
        if not parsed or "title" not in parsed:
            return self._fallback_design_spec(prd_data)
        return parsed

    async def _self_review_design(self, spec: dict) -> dict:
        """Light completeness review — same pattern as ProductDepartment._self_review_prd."""
        result = await self.agent_loop.run_structured(
            task=json.dumps(spec),
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
            # Last resort: raw prd_content as a minimal dict
            prd_content = task.payload.get("prd_content")
            if prd_content:
                return {"title": "Feature", "problem_statement": str(prd_content)}
        return {}

    @staticmethod
    def _fallback_design_spec(prd_data: dict) -> dict:
        title = str(prd_data.get("title") or "Untitled Feature")
        return {
            "title": title,
            "overview": (
                "Design specification to be refined with the team while preserving Engineering handoff contracts."
            ),
            "screens": [
                {
                    "name": "PrimaryActionScreen",
                    "route": "/",
                    "description": "Primary screen where users complete the main feature workflow.",
                    "components_used": ["FeatureForm", "StatusPanel", "ErrorEmptyState"],
                    "data_fetching": (
                        "Fetch initial feature data from the feature API before rendering interactive controls."
                    ),
                },
                {
                    "name": "ErrorEmptyStateScreen",
                    "route": None,
                    "description": (
                        "Fallback screen for empty results, request failures, or unavailable feature data."
                    ),
                    "components_used": ["ErrorEmptyState", "StatusPanel"],
                    "data_fetching": (
                        "Reuse the failed or empty response from the primary feature request."
                    ),
                },
            ],
            "components": [
                {
                    "name": "FeatureForm",
                    "description": "Captures user input and submits the primary feature action.",
                    "props": [
                        {
                            "field_name": "value",
                            "data_type": "string",
                            "source": "local_state",
                            "description": "Current user-entered value for the primary action.",
                            "validation_rules": ["Required before submit"],
                        }
                    ],
                    "interaction_states": [
                        {
                            "state_name": "loading",
                            "trigger": "Submit action starts",
                            "ui_change": "Disable inputs and show an inline progress indicator.",
                        },
                        {
                            "state_name": "error",
                            "trigger": "Submit action fails",
                            "ui_change": (
                                "Show an accessible error summary and keep user input intact."
                            ),
                        },
                    ],
                    "accessibility_notes": (
                        "All controls must be keyboard reachable with visible focus styles."
                    ),
                },
                {
                    "name": "StatusPanel",
                    "description": "Displays success, pending, and system status feedback.",
                    "props": [
                        {
                            "field_name": "status",
                            "data_type": "string",
                            "source": "api",
                            "description": "Current server-confirmed feature status.",
                            "validation_rules": ["Must map to a known display state"],
                        }
                    ],
                    "interaction_states": [
                        {
                            "state_name": "loading",
                            "trigger": "Status refresh starts",
                            "ui_change": "Render skeleton text with polite live-region updates.",
                        },
                        {
                            "state_name": "error",
                            "trigger": "Status refresh fails",
                            "ui_change": (
                                "Render retry guidance and preserve the last known status."
                            ),
                        },
                    ],
                    "accessibility_notes": "Use aria-live=polite for asynchronous status changes.",
                },
                {
                    "name": "ErrorEmptyState",
                    "description": (
                        "Provides recovery actions for empty, failed, or unavailable states."
                    ),
                    "props": [
                        {
                            "field_name": "message",
                            "data_type": "string",
                            "source": "prop",
                            "description": "Human-readable recovery message.",
                            "validation_rules": ["Must be concise and actionable"],
                        }
                    ],
                    "interaction_states": [
                        {
                            "state_name": "loading",
                            "trigger": "Retry action starts",
                            "ui_change": "Replace retry button text with loading feedback.",
                        },
                        {
                            "state_name": "error",
                            "trigger": "Retry action fails",
                            "ui_change": "Keep recovery actions visible and update the error copy.",
                        },
                    ],
                    "accessibility_notes": (
                        "Associate the message with the recovery action for screen readers."
                    ),
                },
            ],
            "global_state_management": (
                "Keep transient form state local, cache API status globally only if multiple screens consume it, and synchronize mutations through a single feature service."
            ),
            "accessibility_checklist": {
                "keyboard_navigation": True,
                "screen_reader_labels": True,
                "color_contrast_aa": True,
                "aria_attributes": True,
                "focus_management": True,
                "notes": (
                    "Target WCAG 2.1 AA, including visible focus, labeled controls, and announced async status changes."
                ),
            },
            "error_boundaries": (
                "Wrap the primary workflow route and status panel in recoverable error boundaries with retry actions."
            ),
        }

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
