import json
import logging
import re
from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable
from aider.company.schemas.design_spec import DesignSpecV2
from aider.company.validators.schema_gate import SchemaGateValidator
from aider.memory import ConversationMemory, ProjectMemory

logger = logging.getLogger(__name__)


class UXDepartment(Department):
    name = "ux"
    allowed_tools = ["aider_coder"]

    def get_context_requirements(self) -> list[str]:
        return [
            "project.prd",
            "project.prd_structured",
            "playbook.ux_preferences",
            "skills.shared",
            "skills.ux",
        ]

    def __init__(
        self,
        project_memory: ProjectMemory,
        agent_loop: AiderAgentLoop,
        conversation_memory: Optional[ConversationMemory] = None,
        config: Optional[DepartmentConfig] = None,
    ):
        super().__init__(project_memory, conversation_memory, config=config)
        self.agent_loop = agent_loop
        self._retry_count: int = 0

    async def process(self, task: CompanyTask) -> Deliverable:
        self._retry_count = 0

        # 1. Generate DesignSpec
        raw_spec = await self._generate_design_spec(task)

        # 2. Schema Gate Validation + Self-Review
        validator = SchemaGateValidator()
        gate_result = validator.validate(raw_spec)

        # Auto-retry once with feedback
        if not gate_result.approved and self._retry_count < 1:
            self._retry_count += 1
            logger.warning(
                "UX Schema Gate failed (attempt %s). Retrying...", self._retry_count
            )
            rejection_feedback = gate_result.to_engineering_rejection_payload()
            raw_spec = await self._generate_design_spec(
                task, feedback=rejection_feedback
            )
            gate_result = validator.validate(raw_spec)

        # 3. Failure path
        if not gate_result.approved:
            logger.error("UX failed schema gate after retry.")
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type="design_spec",
                payload=gate_result.to_engineering_rejection_payload(),
                status="validation_failed",
                metadata={
                    "validation_errors": gate_result.rejection_reasons,
                    "ux_retry_count": self._retry_count,
                },
            )

        # 4. Success path
        structured_spec = (
            gate_result.parsed_spec.model_dump()
            if gate_result.parsed_spec
            else raw_spec
        )

        await self._emit_lifecycle_event(
            task.task_id,
            "ux_design_complete",
            {
                "title": (
                    structured_spec.get("title", "Untitled")
                    if isinstance(structured_spec, dict)
                    else "Untitled"
                ),
                "screens_count": (
                    len(structured_spec.get("screens", []))
                    if isinstance(structured_spec, dict)
                    else 0
                ),
                "components_count": (
                    len(structured_spec.get("components", []))
                    if isinstance(structured_spec, dict)
                    else 0
                ),
                "schema_gate_approved": True,
            },
        )

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="design_spec",
            payload=(
                json.dumps(raw_spec, indent=2)
                if isinstance(raw_spec, dict)
                else str(raw_spec)
            ),
            status="success",
            metadata={
                "design_spec_structured": structured_spec,
                "design_spec_summary": self._summarize_design_spec(structured_spec),
                "ux_self_review_passed": True,
                "schema_gate_approved": True,
                "ux_retry_count": self._retry_count,
            },
        )

    async def _generate_design_spec(
        self, task: CompanyTask, feedback: Optional[str] = None
    ) -> dict:
        """Generate DesignSpecV2 using structured output."""
        prd_context = self._get_prd_context(task)
        schema_name = DesignSpecV2.__name__

        skill_guidance = self._format_skill_guidance(
            task.context if isinstance(task.context, dict) else {}
        )
        system_prompt = (
            "You are an expert UX Designer. Create a complete, structured design "
            "specification. Consult available Procedural Skills when their summaries "
            f"match the UX task.\n\n"
            f"PRD / Requirements:\n{prd_context}\n\n"
            f"Procedural Skills Available:\n{skill_guidance or 'None'}\n\n"
            f"{feedback or ''}\n\n"
            f"Return **only** valid JSON matching the {schema_name} schema. "
            "No extra text or markdown."
        )

        result = await self.agent_loop.run_structured(
            task=(
                "Generate a complete engineering-ready Design Specification using "
                f"{schema_name}."
            ),
            system_prompt=system_prompt,
            model=getattr(self.config, "preferred_model", None)
            or "claude-3-7-sonnet-20250219",
            enable_caching=self.agent_config.enable_caching,
        )

        content = self._result_content(result)

        try:
            if isinstance(content, str):
                # Extract JSON from markdown fences if present.
                match = re.search(
                    r"```(?:json)?\s*(\{.*?\})\s*```",
                    content,
                    re.DOTALL | re.IGNORECASE,
                )
                if match:
                    content = match.group(1)
                return json.loads(content)
            return content
        except Exception as e:
            logger.warning("Failed to parse UX DesignSpec JSON: %s", e)
            # Minimal fallback
            return {
                "title": "Design Specification",
                "overview": (
                    str(content)[:500]
                    if isinstance(content, str)
                    else "Generation failed"
                ),
                "screens": [],
                "components": [],
                "global_state_management": "Unknown",
                "accessibility_checklist": {},
            }

    def _get_prd_context(self, task: CompanyTask) -> str:
        payload = task.payload if isinstance(task.payload, dict) else {}
        context = (
            task.context if isinstance(getattr(task, "context", None), dict) else {}
        )

        prd = context.get("prd_structured") or payload.get("prd_structured")
        if isinstance(prd, dict):
            return (
                f"Title: {prd.get('title')}\n"
                f"Problem: {prd.get('problem_statement')}\n"
                "Acceptance Criteria:\n"
                + "\n".join(f"- {c}" for c in prd.get("acceptance_criteria", []))
            )
        return (
            context.get("prd_content")
            or payload.get("prd_content")
            or payload.get("original_request")
            or str(task.payload)
        )

    @staticmethod
    def _format_skill_guidance(context: dict) -> str:
        guidance = context.get("skill_guidance", [])
        if not guidance:
            return ""
        items = guidance if isinstance(guidance, list) else [guidance]
        return "\n".join(f"- {str(item)[:240]}" for item in items[:5])

    @staticmethod
    def _result_content(result):
        if isinstance(result, dict):
            if "content" in result or "summary" in result:
                return result.get("content") or result.get("summary") or ""
            return result
        return getattr(result, "content", None) or getattr(result, "summary", "") or ""

    @staticmethod
    def _summarize_design_spec(spec) -> str:
        """Human-readable summary for handoffs and reviewer prompts."""
        if isinstance(spec, dict):
            parts = [f"Title: {spec.get('title', 'Untitled Design')}"]
            if overview := spec.get("overview"):
                parts.append(f"Overview: {str(overview)[:200]}")
            if screens := spec.get("screens"):
                parts.append(f"Screens: {len(screens)}")
            if components := spec.get("components"):
                parts.append(f"Components: {len(components)}")
            return "\n".join(parts)
        return str(spec)[:800]
