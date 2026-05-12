"""
ProductDepartment — LLM-powered Product Manager.

Workflow for a new feature request:
  1. Ambiguity check   — detect if the request needs clarification
  2. Clarification     — ask 1–3 targeted questions via ApprovalManager gate
                         (if ambiguous); answers are fed back as context
  3. PRD generation    — structured JSON PRD via run_structured
  4. Self-review       — completeness + measurability pass
  5. Approval prep     — format for CEO approval gate

Engineering clarification memos (task.origin == "engineering") bypass
steps 1–4 and return a simple clarification response as before.

CEO feedback / revision (payload has "previous_prd") bypasses the
ambiguity check and goes straight to PRD generation with the feedback
appended as context.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.department import Department
from aider.company.interfaces import Deliverable
from aider.company.schemas import ClarificationRequest, CompanyTask, PRD
from aider.memory import ConversationMemory, ProjectMemory

_AMBIGUITY_SYSTEM = """\
You are a senior Product Manager evaluating whether a feature request is
specific enough to write a PRD without asking clarifying questions.

Respond with a JSON object only — no markdown, no preamble:
{"needs_clarification": true|false, "reason": "<one sentence>"}

A request needs clarification when ANY of these are missing:
- Who are the target users?
- What problem does this solve?
- What does "done" look like (even roughly)?
- Are there obvious technical constraints or integrations mentioned?

Short requests that name a clear deliverable (e.g. "add email notifications
for new comments") do NOT need clarification.
"""

_CLARIFICATION_SYSTEM = """\
You are a senior Product Manager. Generate 2–3 targeted clarification
questions that will unblock writing a complete PRD.

Rules:
- Each question should unlock a different dimension (who/what/done/constraint).
- Questions must be specific to THIS request, not generic.
- Do not ask for things that are obvious from the request itself.

Respond with a JSON object only — no markdown, no preamble:
{"questions": ["<question 1>", "<question 2>", "<question 3 (optional)>"]}
"""

_PRD_SYSTEM = """\
You are an expert Product Manager. Write a complete, professional PRD.

Return a JSON object only — no markdown fences, no preamble — using
exactly these keys:
{
  "title": "<feature name>",
  "problem_statement": "<1–2 sentences>",
  "goals": ["<goal>", ...],
  "success_metrics": ["<measurable metric>", ...],
  "user_stories": ["As a <user>, I want <action> so that <outcome>", ...],
  "acceptance_criteria": ["<testable criterion>", ...],
  "technical_considerations": ["<consideration>", ...],
  "out_of_scope": ["<item>", ...],
  "priority": "MVP",
  "open_questions": ["<question if genuinely unknown>", ...]
}

Rules:
- acceptance_criteria must be testable (Given/When/Then style preferred).
- success_metrics must be measurable (include numbers or events).
- out_of_scope must include at least one item to set expectations.
- open_questions should be empty [] unless something is genuinely unresolved.
- Do not add fields beyond those listed.
"""

_REVIEW_SYSTEM = """\
You are a senior Product Manager reviewing a PRD for quality.

Check for these problems only:
1. acceptance_criteria items that are not testable (vague words: "works well",
   "is fast", "looks good")
2. success_metrics that are not measurable (no number, no event, no threshold)
3. Missing user_stories (empty or fewer than 2)
4. problem_statement that does not name a real user pain

Return a JSON object only — no markdown, no preamble:
{
  "issues": ["<issue description>", ...],
  "improved_prd": { <full PRD JSON with fixes applied, same schema as input> }
}

If no issues are found, return:
{"issues": [], "improved_prd": null}
"""


class ProductDepartment(Department):
    """LLM-powered Product Manager department."""

    name = "product"
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
        return ["playbook.*", "project.name", "project.phase"]

    async def process(self, task: CompanyTask) -> Deliverable:
        if task.origin == "engineering" or task.artifact_type == "memo":
            return self._process_engineering_clarification(task)

        if isinstance(task.payload, dict) and "previous_prd" in task.payload:
            return await self._process_prd_revision(task)

        original_request = self._original_request(task.payload)
        has_clarification_answers = (
            isinstance(task.payload, dict) and "clarification_answers" in task.payload
        )
        if not has_clarification_answers:
            needs_clarification = await self._needs_clarification(original_request)

            if needs_clarification:
                questions = await self._ask_clarification_questions(original_request)
                return self._make_clarification_deliverable(
                    task, original_request, questions
                )

        prd_context = dict(task.context)
        if has_clarification_answers and isinstance(task.payload, dict):
            prd_context.setdefault(
                "clarification_answers", task.payload.get("clarification_answers", "")
            )
        prd = await self._generate_prd(original_request, prd_context)
        prd = await self._self_review_prd(prd)
        return self._make_prd_deliverable(task, prd, original_request)

    async def _needs_clarification(self, original_request: str) -> bool:
        """Return True if the request is too vague to write a PRD."""
        result = await self.agent_loop.run_structured(
            task=f"Feature request:\n{original_request}",
            system_prompt=_AMBIGUITY_SYSTEM,
            enable_caching=self.config.enable_prompt_caching,
        )
        parsed = self._parse_json(result.get("content", ""))
        return bool(parsed.get("needs_clarification", False))

    async def _ask_clarification_questions(self, original_request: str) -> list[str]:
        """Return 2–3 targeted clarification questions."""
        result = await self.agent_loop.run_structured(
            task=f"Feature request:\n{original_request}",
            system_prompt=_CLARIFICATION_SYSTEM,
            enable_caching=self.config.enable_prompt_caching,
        )
        parsed = self._parse_json(result.get("content", ""))
        questions = parsed.get("questions", [])
        if not isinstance(questions, list) or not questions:
            questions = self._extract_questions_fallback(result.get("content", ""))
        if not questions:
            questions = [
                "Who are the target users for this request?",
                "What user problem should this solve first?",
                "What should count as done for the first usable version?",
            ]
        return [str(q) for q in questions[:3]]

    async def _generate_prd(self, original_request: str, context: dict) -> PRD:
        """Generate a structured PRD from the request and project context."""
        parts = []
        playbook_text = self._format_playbook(context)
        if playbook_text:
            parts.append(f"Playbook guidance:\n{playbook_text}")
        clarification_answers = context.get("clarification_answers")
        if clarification_answers:
            parts.append(f"CEO clarification answers:\n{clarification_answers}")
        parts.append(f"Feature request:\n{original_request}")
        task_text = "\n\n".join(parts)

        result = await self.agent_loop.run_structured(
            task=task_text,
            system_prompt=_PRD_SYSTEM,
            enable_caching=self.config.enable_prompt_caching,
            model=self.config.preferred_model or None,
        )
        parsed = self._parse_json(result.get("content", ""))
        if not parsed or "title" not in parsed:
            return PRD(
                title=original_request[:80],
                problem_statement=original_request,
                goals=["Define goals with the team."],
                acceptance_criteria=["To be defined."],
            )
        return PRD.from_dict(parsed)

    async def _self_review_prd(self, prd: PRD) -> PRD:
        """Light completeness + measurability review. Returns improved PRD."""
        result = await self.agent_loop.run_structured(
            task=json.dumps(prd.to_dict()),
            system_prompt=_REVIEW_SYSTEM,
            enable_caching=self.config.enable_prompt_caching,
        )
        parsed = self._parse_json(result.get("content", ""))
        if not parsed:
            return prd
        improved_raw = parsed.get("improved_prd")
        if improved_raw and isinstance(improved_raw, dict) and "title" in improved_raw:
            return PRD.from_dict(improved_raw)
        return prd

    async def _process_prd_revision(self, task: CompanyTask) -> Deliverable:
        """Re-generate PRD incorporating prior PRD context and CEO feedback."""
        payload = task.payload if isinstance(task.payload, dict) else {}
        previous_prd = payload.get("previous_prd")
        previous_prd_structured = payload.get("previous_prd_structured")
        ceo_feedback = str(payload.get("ceo_feedback") or "Please revise.")
        reviewer_notes = str(
            payload.get("reviewer_notes") or task.context.get("reviewer_notes") or ""
        )
        revision_count = max(1, self._revision_count(payload))
        original_request = str(
            payload.get("original_request")
            or task.context.get("original_request")
            or ""
        )
        previous_prd_summary = self._summarize_previous_prd(
            previous_prd_structured or previous_prd
        )

        await self._emit_lifecycle_event(
            task.task_id,
            "product_revision_start",
            {
                "formatted": "Product is revising the PRD based on feedback…",
                "revision_count": revision_count,
                "feedback": ceo_feedback,
            },
        )

        task_text = self._build_prd_revision_prompt(
            original_request=original_request,
            previous_prd=previous_prd,
            previous_prd_structured=previous_prd_structured,
            ceo_feedback=ceo_feedback,
            reviewer_notes=reviewer_notes,
            revision_count=revision_count,
            context=task.context,
        )
        result = await self.agent_loop.run_structured(
            task=task_text,
            system_prompt=_PRD_SYSTEM,
            enable_caching=self.config.enable_prompt_caching,
            model=self.config.preferred_model or None,
        )
        parsed = self._parse_json(result.get("content", ""))
        if parsed and "title" in parsed:
            prd = PRD.from_dict(parsed)
        else:
            prd = PRD(
                title="PRD (revised)",
                problem_statement=original_request,
            )
        prd.version = f"1.{revision_count}"
        prd.revision_count = revision_count
        prd.previous_prd_summary = previous_prd_summary
        prd = await self._self_review_prd_on_revision(
            prd,
            previous_prd=previous_prd,
            ceo_feedback=ceo_feedback,
            reviewer_notes=reviewer_notes,
            revision_count=revision_count,
        )
        prd.revision_count = revision_count
        prd.previous_prd_summary = previous_prd_summary
        deliverable = self._make_prd_deliverable(task, prd, original_request)
        deliverable.metadata["ceo_feedback"] = ceo_feedback
        deliverable.metadata["previous_prd_summary"] = previous_prd_summary
        if reviewer_notes:
            deliverable.metadata["reviewer_notes"] = reviewer_notes
        await self._emit_lifecycle_event(
            task.task_id,
            "product_prd_revised",
            {
                "formatted": "Product revised the PRD and sent it back for approval.",
                "revision_count": revision_count,
                "prd_title": prd.title,
            },
        )
        return deliverable

    def _build_prd_revision_prompt(
        self,
        *,
        original_request: str,
        previous_prd,
        previous_prd_structured,
        ceo_feedback: str,
        reviewer_notes: str,
        revision_count: int,
        context: dict,
    ) -> str:
        """Build a revision prompt that preserves prior PRD and feedback context."""
        parts = [
            f"Original request:\n{original_request or 'Not provided.'}",
            f"Revision round: {revision_count}",
        ]
        playbook_text = self._format_playbook(context)
        if playbook_text:
            parts.append(f"Relevant playbook guidance:\n{playbook_text}")
        structured = previous_prd_structured
        if isinstance(previous_prd, PRD):
            structured = previous_prd.to_dict()
        elif structured is None and isinstance(previous_prd, dict):
            structured = previous_prd
        if structured:
            parts.append(
                "Previous PRD (structured JSON):\n"
                + json.dumps(structured, indent=2, sort_keys=True)
            )
        previous_markdown = self._previous_prd_markdown(previous_prd, structured)
        if previous_markdown:
            parts.append(f"Previous PRD (markdown):\n{previous_markdown}")
        parts.append(f"CEO / clarification feedback:\n{ceo_feedback}")
        if reviewer_notes:
            parts.append(f"Reviewer notes:\n{reviewer_notes}")
        parts.append(
            "Rewrite the PRD addressing all feedback. Preserve useful prior decisions, "
            "make changed requirements explicit, and keep acceptance criteria testable."
        )
        return "\n\n".join(parts)

    async def _self_review_prd_on_revision(
        self,
        prd: PRD,
        *,
        previous_prd,
        ceo_feedback: str,
        reviewer_notes: str,
        revision_count: int,
    ) -> PRD:
        """Light revision-specific quality review before emitting a revised PRD."""
        review_payload = {
            "revision_count": revision_count,
            "ceo_feedback": ceo_feedback,
            "reviewer_notes": reviewer_notes,
            "previous_prd_summary": self._summarize_previous_prd(previous_prd),
            "revised_prd": prd.to_dict(),
        }
        result = await self.agent_loop.run_structured(
            task=json.dumps(review_payload),
            system_prompt=_REVIEW_SYSTEM,
            enable_caching=self.config.enable_prompt_caching,
        )
        parsed = self._parse_json(result.get("content", ""))
        if not parsed:
            return prd
        improved_raw = parsed.get("improved_prd")
        if improved_raw and isinstance(improved_raw, dict) and "title" in improved_raw:
            return PRD.from_dict(improved_raw)
        return prd

    def _previous_prd_markdown(self, previous_prd, structured=None) -> str:
        if isinstance(previous_prd, PRD):
            return previous_prd.to_markdown()
        if isinstance(previous_prd, str):
            return previous_prd
        if isinstance(structured, dict):
            return PRD.from_dict(structured).to_markdown()
        return str(previous_prd or "")

    def _summarize_previous_prd(self, previous_prd) -> str:
        if isinstance(previous_prd, PRD):
            return f"{previous_prd.title}: {previous_prd.problem_statement}"[:500]
        if isinstance(previous_prd, dict):
            return (
                f"{previous_prd.get('title', '')}: "
                f"{previous_prd.get('problem_statement') or previous_prd.get('overview', '')}"
            )[:500]
        return str(previous_prd or "")[:500]

    def _process_engineering_clarification(self, task: CompanyTask) -> Deliverable:
        question = self._clarification_question(task.payload)
        response = f"Product clarification: {question}"
        context = dict(task.context)
        context["last_clarification_question"] = question
        context["last_clarification_response"] = response
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="memo",
            payload=response,
            status="success",
            metadata={
                "handoff_to": "engineering",
                "next_artifact_type": "memo",
                "blocking": False,
                "context": context,
            },
        )

    def _make_prd_deliverable(
        self, task: CompanyTask, prd: PRD, original_request: str
    ) -> Deliverable:
        prd_markdown = prd.to_markdown()
        requires_design = self._requires_design(task.payload, original_request)
        handoff_to = "ux" if requires_design else "engineering"
        context = dict(task.context)
        context["original_request"] = original_request
        context["prd_structured"] = prd.to_dict()
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="prd",
            payload=prd_markdown,
            status="success",
            metadata={
                "handoff_to": handoff_to,
                "next_artifact_type": "prd",
                "blocking": True,
                "gate_name": "prd_approval",
                "original_request": original_request,
                "revision_count": self._revision_count(task.payload),
                "requires_design": requires_design,
                "prd_structured": prd.to_dict(),
                "prd_version": prd.version,
                "open_questions": prd.open_questions,
                "artifact_preview": prd_markdown[:1500],
                "context": context,
            },
        )

    def _make_clarification_deliverable(
        self,
        task: CompanyTask,
        original_request: str,
        questions: list[str],
    ) -> Deliverable:
        clarification = ClarificationRequest(
            questions=questions,
            original_request=original_request,
            task_id=task.task_id,
        )
        context = dict(task.context)
        context["clarification_request"] = clarification.to_dict()
        context["original_request"] = original_request
        preview = clarification.format_for_approval()
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="clarification",
            payload=preview,
            status="needs_review",
            metadata={
                "handoff_to": "ceo",
                "blocking": True,
                "gate_name": "clarification_approval",
                "approver_role": "ceo",
                "clarification_questions": questions,
                "original_request": original_request,
                "artifact_preview": preview[:1500],
                "context": context,
            },
        )

    @staticmethod
    def _original_request(payload) -> str:
        if isinstance(payload, dict):
            return (
                payload.get("original_request")
                or payload.get("prompt")
                or payload.get("description")
                or str(payload)
            )
        return str(payload)

    @staticmethod
    def _revision_count(payload) -> int:
        if isinstance(payload, dict):
            return int(payload.get("revision_count", 0) or 0)
        return 0

    @staticmethod
    def _clarification_question(payload) -> str:
        if isinstance(payload, dict):
            return payload.get("question") or payload.get("description") or str(payload)
        return str(payload)

    @staticmethod
    def _requires_design(payload, original_request: str) -> bool:
        if isinstance(payload, dict) and "requires_design" in payload:
            return bool(payload.get("requires_design"))
        prompt_terms = set(re.findall(r"[a-z0-9]+", original_request.lower()))
        design_terms = {
            "ui",
            "ux",
            "design",
            "wireframe",
            "wireframes",
            "frontend",
            "screen",
            "screens",
            "dashboard",
            "component",
            "components",
            "layout",
            "css",
        }
        return (
            bool(prompt_terms & design_terms) or "front-end" in original_request.lower()
        )

    @staticmethod
    def _format_playbook(context: dict) -> str:
        guidance = context.get("playbook_guidance", [])
        if not guidance:
            return ""
        return "\n".join(f"- {item}" for item in guidance[:10])

    @staticmethod
    def _parse_json(content: str) -> dict:
        """
        Extract a JSON object from LLM output.

        Tries strict parse first, then strips markdown fences,
        then extracts the first {...} block. Returns {} on failure.
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

    @staticmethod
    def _extract_questions_fallback(content: str) -> list[str]:
        """Extract numbered/bulleted questions when JSON parse fails."""
        lines = content.splitlines()
        questions = []
        for line in lines:
            stripped = re.sub(r"^[\s\-\*\d\.\)]+", "", line).strip()
            if len(stripped) > 15 and "?" in stripped:
                questions.append(stripped)
        return questions[:3]
