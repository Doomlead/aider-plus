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
        """Re-generate PRD incorporating CEO feedback."""
        previous_prd = task.payload.get("previous_prd", "")
        ceo_feedback = task.payload.get("ceo_feedback", "Please revise.")
        revision_count = self._revision_count(task.payload)
        original_request = task.payload.get("original_request", "")

        task_text = (
            f"Previous PRD:\n{previous_prd}\n\n"
            f"CEO Feedback (revision {revision_count}):\n{ceo_feedback}\n\n"
            f"Original request:\n{original_request}\n\n"
            "Rewrite the PRD addressing all CEO feedback. Keep what was good."
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
            prd.version = f"1.{revision_count}"
        else:
            prd = PRD(
                title="PRD (revised)",
                problem_statement=original_request,
                version=f"1.{revision_count}",
            )
        return self._make_prd_deliverable(task, prd, original_request)

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
        return bool(prompt_terms & design_terms) or "front-end" in original_request.lower()

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
