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
from aider.company.schemas import CompanyTask, DesignSpec
from aider.memory import ConversationMemory, ProjectMemory


_UX_SYSTEM_PROMPT = """\
You are an expert UX Designer and product thinker.
Given a PRD, produce a clear, actionable Design Specification.

Return ONLY a valid JSON object — no markdown fences, no preamble — using
exactly these keys:
{
  "title": "<feature name matching the PRD title>",
  "overview": "<2-3 sentence design philosophy for this feature>",
  "key_screens": ["<screen name and one-line purpose>", ...],
  "component_library": [
    {"name": "<ComponentName>", "description": "<what it does>", "variants": ["<variant>", ...]},
    ...
  ],
  "user_flows": ["<step-by-step flow description>", ...],
  "accessibility_notes": ["<WCAG or UX accessibility requirement>", ...],
  "technical_requirements": ["<constraint for Engineering>", ...],
  "visual_style": {
    "primary_color": "<hex or CSS var>",
    "surface_color": "<hex or CSS var>",
    "typography": "<font stack or description>",
    "spacing_unit": "<rem or px value>"
  },
  "version": "1.0"
}

Rules:
- key_screens must include at minimum: the primary action screen and an error/empty state.
- component_library must list at least 3 components with their variants.
- user_flows must describe at least one complete end-to-end flow.
- accessibility_notes must reference keyboard navigation and at least one WCAG criterion.
- technical_requirements must list constraints Engineering needs (API shape, state management, etc.).
- Do not add keys beyond those listed.
"""

_REVIEW_SYSTEM = """\
You are a senior UX Designer reviewing a Design Specification for completeness.

Check for these problems only:
1. key_screens with fewer than 2 entries
2. component_library entries missing a name or description
3. user_flows that don't describe a complete end-to-end interaction
4. accessibility_notes that are empty or generic placeholders
5. technical_requirements that are empty

Return a JSON object only — no markdown, no preamble:
{
  "issues": ["<issue description>", ...],
  "improved_spec": { <full DesignSpec JSON with fixes applied, same schema as input> }
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
        design_spec = await self._generate_design_spec(prd_data, task)
        design_spec = await self._self_review_design(design_spec)

        spec_markdown = design_spec.to_markdown()
        context = dict(task.context)
        context["design_spec"] = design_spec.to_dict()
        context["design_spec_structured"] = design_spec.to_dict()

        await self._emit_lifecycle_event(
            "ux_complete",
            {
                "title": design_spec.title,
                "key_screens": len(design_spec.key_screens),
                "components": len(design_spec.component_library),
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
                "design_spec_structured": design_spec.to_dict(),
                "design_version": design_spec.version,
                "context": context,
            },
        )

    # ------------------------------------------------------------------
    # Stage handlers
    # ------------------------------------------------------------------

    async def _generate_design_spec(
        self, prd_data: dict, task: CompanyTask
    ) -> DesignSpec:
        """Generate a structured DesignSpec from the PRD dict or raw payload."""
        if prd_data:
            context_text = json.dumps(prd_data, indent=2)
        elif isinstance(task.payload, str):
            context_text = task.payload
        else:
            context_text = json.dumps(task.payload, indent=2, default=str)

        result = await self.agent_loop.run_structured(
            task=f"PRD to design:\n{context_text}",
            system_prompt=_UX_SYSTEM_PROMPT,
            enable_caching=self.config.enable_prompt_caching,
            model=self.config.preferred_model or None,
        )
        parsed = self._parse_json(result.get("content", ""))
        if not parsed or "title" not in parsed:
            # Minimal fallback — better than crashing
            return DesignSpec(
                title=prd_data.get("title", "Untitled Feature"),
                overview="Design specification to be refined with the team.",
                key_screens=["Primary action screen", "Error / empty state"],
                accessibility_notes=["Follow WCAG 2.1 AA guidelines."],
                technical_requirements=["To be defined with Engineering."],
            )
        return DesignSpec.from_dict(parsed)

    async def _self_review_design(self, spec: DesignSpec) -> DesignSpec:
        """Light completeness review — same pattern as ProductDepartment._self_review_prd."""
        result = await self.agent_loop.run_structured(
            task=json.dumps(spec.to_dict()),
            system_prompt=_REVIEW_SYSTEM,
            enable_caching=self.config.enable_prompt_caching,
        )
        parsed = self._parse_json(result.get("content", ""))
        if not parsed:
            return spec
        improved_raw = parsed.get("improved_spec")
        if improved_raw and isinstance(improved_raw, dict) and "title" in improved_raw:
            return DesignSpec.from_dict(improved_raw)
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
