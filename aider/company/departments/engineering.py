import asyncio
import inspect
import json
import logging
import re
import shlex
import uuid
from pathlib import Path
from typing import Optional

from aider.company.department import Department
from aider.company.config import DepartmentConfig
from aider.company.schemas import CompanyTask, Deliverable
from aider.agent.loop import AiderAgentLoop
from aider.memory import ProjectMemory, ConversationMemory
from aider.memory.pattern_extractor import AuditPatternExtractor

logger = logging.getLogger(__name__)


class EngineeringDepartment(Department):
    name = "engineering"
    allowed_tools = ["aider_coder", "file_read", "file_write"]
    max_internal_iterations = 3

    def get_context_requirements(self) -> list[str]:
        return [
            "project.prd",
            "project.design_spec",
            "playbook.coding_standards",
            "playbook.ux_preferences",
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
        self.tools = ["aider_coder"]
        self.current_stage: str = "programmer"
        self._active_task: Optional[CompanyTask] = None
        self._review_feedback: Optional[dict] = None
        self._revision_count: int = 0
        self._last_reviewer_issues: Optional[str] = None
        if hasattr(self.agent_loop, "tool_registry"):
            self.agent_loop.tool_registry.set_department(self)

    async def process(self, task: CompanyTask) -> Deliverable:
        self._active_task = task
        self._review_feedback = None
        self._revision_count = 0
        self._last_reviewer_issues = None
        last_programmer_deliverable: Optional[Deliverable] = None
        last_review: Optional[Deliverable] = None

        for iteration in range(1, self.max_internal_iterations + 1):
            await self._emit_engineering_event(
                task.task_id,
                "engineering_programmer_start",
                {
                    "iteration": iteration,
                    "max_iterations": self.max_internal_iterations,
                },
            )
            programmer_deliverable = await self._run_programmer_phase(
                task, previous_deliverable=last_review
            )
            last_programmer_deliverable = programmer_deliverable

            await self._emit_engineering_event(
                task.task_id,
                "engineering_reviewer_start",
                {
                    "iteration": iteration,
                    "files": programmer_deliverable.metadata.get("files", []),
                },
            )
            review = await self._run_reviewer_phase(programmer_deliverable)
            last_review = review
            self._review_feedback = review.review_feedback or review.metadata.get("review_feedback")
            max_review_iterations = int(
                getattr(self.config, "max_review_iterations", self.max_internal_iterations)
                or self.max_internal_iterations
            )
            if (
                not review.review_passed
                and review.metadata.get("review_iteration", iteration) >= max_review_iterations
            ):
                logger.warning("Max review iterations reached, forcing approval")
                forced_metadata = dict(review.metadata)
                forced_metadata.update(
                    {
                        "forced_approval": True,
                        "review_passed": True,
                        "needs_revision": False,
                    }
                )
                review = Deliverable(
                    task_id=review.task_id,
                    department=review.department,
                    artifact_type=review.artifact_type,
                    payload=review.payload,
                    status="success",
                    metadata=forced_metadata,
                    review_feedback=review.review_feedback,
                    review_passed=True,
                )
                last_review = review

            if review.review_passed:
                await self._emit_engineering_event(
                    task.task_id,
                    "engineering_review_approved",
                    {
                        "iteration": iteration,
                        "files": review.metadata.get("files", []),
                        "checks": review.metadata.get("review_checks", []),
                        "reviewer_feedback_summary": review.metadata.get(
                            "reviewer_feedback_summary", ""
                        ),
                    },
                )
                return review

            self._last_reviewer_issues = self._review_feedback_summary(self._review_feedback)

            await self._emit_engineering_event(
                task.task_id,
                "engineering_revision_needed",
                {
                    "iteration": iteration,
                    "feedback": self._review_feedback,
                    "reviewer_feedback_summary": review.metadata.get(
                        "reviewer_feedback_summary", ""
                    ),
                    "remaining_iterations": self.max_internal_iterations - iteration,
                },
            )

        fallback = last_review or last_programmer_deliverable
        if fallback is None:
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type="code",
                payload="Engineering did not produce a deliverable.",
                status="failure",
                metadata={
                    "review_passed": False,
                    "revision_count": self._revision_count,
                    "last_reviewer_issues": self._last_reviewer_issues,
                },
                review_passed=False,
            )

        metadata = dict(fallback.metadata)
        metadata.update(
            {
                "review_passed": False,
                "review_feedback": self._review_feedback,
                "max_internal_iterations": self.max_internal_iterations,
                "revision_count": self._revision_count,
                "last_reviewer_issues": self._last_reviewer_issues,
            }
        )
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="code",
            payload=fallback.payload,
            status="failure",
            metadata=metadata,
            review_feedback=self._review_feedback,
            review_passed=False,
        )

    async def _run_programmer_phase(
        self, task: CompanyTask, previous_deliverable: Optional[Deliverable] = None
    ) -> Deliverable:
        """Programmer phase: incorporate PRD + DesignSpec + previous reviewer feedback."""
        self.current_stage = "programmer"

        base_instruction = self._extract_instruction(task)
        prd_text = self._get_prd_context(task)
        design_text = self._get_design_context(task)
        reviewer_feedback = ""
        reviewer_feedback_data = None

        if self._deliverable_needs_revision(previous_deliverable):
            previous_revision_count = previous_deliverable.metadata.get("revision_count", 0)
            revision_count = previous_revision_count + 1
            reviewer_feedback_data = self._feedback_data(previous_deliverable)
            reviewer_feedback = self._format_review_feedback(previous_deliverable)
            feedback_summary = self._review_feedback_summary(previous_deliverable)
            self._last_reviewer_issues = feedback_summary
            await self._emit_engineering_event(
                task.task_id,
                "programmer_revision_start",
                {
                    "revision_count": revision_count,
                    "last_reviewer_issues_count": len(self._feedback_issues(previous_deliverable)),
                    "last_reviewer_issues": feedback_summary,
                    "has_reviewer_feedback": True,
                    "has_previous_feedback": True,
                },
            )
        else:
            revision_count = 1

        full_prompt = f"""Original Request / PRD:
{prd_text}

Design Specification:
{design_text}

{reviewer_feedback}

Task:
{base_instruction}

Implement this feature following the PRD and design spec above.
Address ALL reviewer feedback from the previous round if present.
"""

        full_prompt = self._append_qa_feedback(full_prompt, task)

        record_department_memory = not self._uses_agent_conversation_memory()
        if record_department_memory:
            self.conversation.add(role="user", content=full_prompt)

        result = await self._execute_programmer_coder(full_prompt, task)

        content = self._result_content(result)
        if content and record_department_memory:
            self.conversation.add(role="assistant", content=content)

        metadata = self._result_metadata(result)
        changed_files = metadata.get("changed_files") or metadata.get("files") or []
        diffs_summary = self._result_diffs_summary(result, metadata)
        commits = metadata.get("commits") or []
        result_metadata = self._raw_result_metadata(result)
        if result_metadata:
            metadata.update(result_metadata)

        metadata.update(
            {
                "stage": "programmer",
                "changed_files": changed_files,
                "files": changed_files,
                "diffs_summary": diffs_summary,
                "commits": commits,
                "revision_count": revision_count,
                "review_feedback_applied": reviewer_feedback_data,
                "last_reviewer_issues": (
                    self._review_feedback_summary(previous_deliverable)
                    if previous_deliverable
                    else None
                ),
                "used_prd": bool(prd_text),
                "used_design_spec": bool(design_text),
                "cache_enabled": self._caching_enabled(),
            }
        )
        self._revision_count = revision_count

        await self._emit_engineering_event(
            task.task_id,
            "programmer_complete",
            {
                "revision_count": revision_count,
                "files_changed": len(changed_files),
                "used_design_spec": bool(design_text),
            },
        )

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="code",
            payload=content,
            status="failure" if self._result_error(result) else "success",
            metadata=metadata,
        )

    async def _execute_programmer_coder(self, full_prompt: str, task: CompanyTask):
        """Run the Architect → Editor programmer coder with the rich prompt."""
        return await self._run_agent_loop(
            full_prompt,
            enable_caching=self._caching_enabled(),
        )

    def _get_prd_context(self, task: CompanyTask) -> str:
        """Extract structured or raw PRD from context/payload."""
        payload = task.payload if isinstance(task.payload, dict) else {}
        context = task.context if isinstance(task.context, dict) else {}
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
            or context.get("prd_summary")
            or payload.get("prd_summary")
            or payload.get("original_request")
            or str(task.payload)
        )

    def _get_design_context(self, task: CompanyTask) -> str:
        """Extract structured or raw DesignSpec from context/payload."""
        payload = task.payload if isinstance(task.payload, dict) else {}
        context = task.context if isinstance(task.context, dict) else {}
        spec = context.get("design_spec_structured") or payload.get("design_spec_structured")
        if isinstance(spec, dict):
            return (
                f"Design Title: {spec.get('title')}\n"
                f"Overview: {spec.get('overview')}\n"
                f"Key Screens: {spec.get('key_screens', [])}"
            )
        return context.get("design_spec") or payload.get("design_spec") or ""

    @staticmethod
    def _extract_instruction(task: CompanyTask) -> str:
        """Get the core instruction from various possible locations."""
        if isinstance(task.payload, dict):
            return (
                task.payload.get("instruction") or task.payload.get("prompt") or str(task.payload)
            )
        return str(task.payload)

    @staticmethod
    def _raw_result_metadata(result) -> dict:
        if isinstance(result, dict):
            return dict(result.get("metadata") or {})
        return dict(getattr(result, "metadata", None) or {})

    @staticmethod
    def _result_diffs_summary(result, metadata: dict) -> str:
        if isinstance(result, dict):
            coder_result = result.get("coder_result") or {}
            return (
                result.get("diffs_summary")
                or coder_result.get("diffs_summary")
                or metadata.get("diffs_summary")
                or EngineeringDepartment._join_diffs(metadata.get("diffs"))
                or ""
            )
        return (
            getattr(result, "diffs_summary", None)
            or metadata.get("diffs_summary")
            or EngineeringDepartment._join_diffs(metadata.get("diffs"))
            or ""
        )

    @staticmethod
    def _join_diffs(diffs) -> str:
        if isinstance(diffs, str):
            return diffs
        if isinstance(diffs, list):
            return "\n".join(str(diff) for diff in diffs if diff)
        return ""

    def _append_qa_feedback(self, prompt: str, task: CompanyTask) -> str:
        """Append structured QA feedback when Engineering is handling a QA revision."""
        qa_feedback_dict = (
            task.context.get("qa_feedback") if isinstance(task.context, dict) else None
        )
        if not qa_feedback_dict:
            return prompt

        from aider.company.schemas import QAFeedback

        fb = QAFeedback.from_dict(qa_feedback_dict)
        qa_section = (
            f"\n\n## QA Revision {fb.revision_number} — Fix Required\n\n"
            "The following tests failed and must be fixed before re-submission:\n"
            + "\n".join(f"- {t}" for t in fb.failed_tests)
            + f"\n\n**Failure output (truncated):**\n```\n{fb.failure_output[:1500]}\n```\n\n"
            "**Recommended fixes:**\n"
            + "\n".join(f"- {r}" for r in fb.recommended_fixes)
            + f"\n\n**PRD reminder:** {fb.prd_excerpt}\n"
        )
        return prompt + qa_section

    async def _run_reviewer_phase(self, previous_deliverable: Deliverable) -> Deliverable:
        """Run intelligent review with full context, iteration safety, and forced approval."""
        self.current_stage = "reviewer"
        await self._resolve_task(previous_deliverable.task_id)

        metadata = dict(previous_deliverable.metadata)
        changed_files = await self._changed_files(metadata)
        diff = await self._implementation_diff(metadata)
        checks = await self._run_targeted_checks(changed_files)

        review_context = self._review_context()
        review_context.update(
            {
                "programmer_diffs": diff or metadata.get("diffs_summary", ""),
                "changed_files": changed_files,
                "targeted_checks": checks,
                "programmer_status": previous_deliverable.status,
            }
        )

        reviewer_result = await self._run_structured_reviewer(review_context)
        review_data = self._parse_reviewer_output(reviewer_result)

        # Append failing checks as blocking issues
        for check in checks:
            if check.get("status") != "failed":
                continue
            description = f"Reviewer check failed: {check.get('name')}"
            if not any(
                issue.get("description") == description
                for issue in review_data.get("issues", [])
            ):
                review_data.setdefault("issues", []).append(
                    {
                        "file": None,
                        "line_range": None,
                        "severity": "critical" if check.get("required", True) else "medium",
                        "description": description,
                        "suggestion": check.get("output") or "Fix the failing check.",
                    }
                )
                if check.get("required", True):
                    review_data["needs_revision"] = True
                    review_data["review_passed"] = False

        reviewer_feedback_summary = str(review_data.get("overall_assessment", ""))[:500]

        # === Iteration Control & Forced Approval ===
        review_iteration = int(metadata.get("review_iteration", 0) or 0) + 1
        max_iterations = getattr(self.config, "max_review_iterations", 3) if self.config else 3
        max_iterations = int(max_iterations or 3)

        forced_approval = False
        if review_iteration >= max_iterations and not review_data.get("review_passed", False):
            forced_approval = True
            review_data["review_passed"] = True
            review_data["needs_revision"] = False
            review_data["overall_assessment"] = (
                review_data.get("overall_assessment", "")
                + f"\n\n[FORCED APPROVAL after {max_iterations} review iterations. "
                "Manual executive review strongly recommended before release.]"
            )
            await self._emit_engineering_event(
                previous_deliverable.task_id,
                "reviewer_forced_approval",
                {
                    "review_iteration": review_iteration,
                    "max_iterations": max_iterations,
                    "warning": (
                        "Forced approval due to iteration limit - manual review strongly "
                        "recommended"
                    ),
                    "severity": "warning",
                },
            )

        metadata.update(
            {
                "stage": "reviewer",
                "review_iteration": review_iteration,
                "files": changed_files,
                "changed_files": changed_files,
                "diffs": [diff] if diff else metadata.get("diffs", []),
                "diffs_summary": review_context.get("programmer_diffs", ""),
                "review_prompt": self._get_reviewer_system_prompt(review_context),
                "review_checks": checks,
                "issues": review_data.get("issues", []),
                "overall_assessment": review_data.get("overall_assessment", ""),
                "review_feedback": review_data,
                "review_passed": review_data.get("review_passed", False),
                "needs_revision": review_data.get("needs_revision", False),
                "reviewer_feedback_summary": reviewer_feedback_summary,
                "used_design_spec": bool(review_context.get("design_spec_summary")),
                "forced_approval": forced_approval,
            }
        )
        metadata["cache_enabled"] = self._caching_enabled(role="reviewer")

        payload = previous_deliverable.payload
        if review_data.get("needs_revision"):
            payload = (
                f"{previous_deliverable.payload}\n\n"
                "Reviewer requested revisions:\n"
                f"{self._format_review_feedback(review_data)}"
            )

        await self._emit_engineering_event(
            previous_deliverable.task_id,
            "reviewer_complete",
            {
                "review_passed": review_data.get("review_passed", False),
                "needs_revision": review_data.get("needs_revision", False),
                "issue_count": len(review_data.get("issues", [])),
                "review_iteration": review_iteration,
                "forced_approval": forced_approval,
                "used_design_spec": bool(review_context.get("design_spec_summary")),
                "summary": review_data.get("overall_assessment", "")[:300],
                "reviewer_feedback_summary": reviewer_feedback_summary,
            },
        )

        # Record metrics and learn systemic patterns only
        self._record_reviewer_metrics(review_data, review_iteration)
        self._learn_reviewer_playbook_pattern(review_data, previous_deliverable.task_id)

        status = "success" if review_data.get("review_passed") else "needs_revision"

        return Deliverable(
            task_id=previous_deliverable.task_id,
            department=self.name,
            artifact_type="code",
            payload=payload,
            status=status,
            metadata=metadata,
            review_feedback=review_data,
            review_passed=review_data.get("review_passed", False),
        )

    async def _run_agent_loop(
        self,
        task_text: str,
        *,
        enable_caching: bool,
    ):
        """Run the agent loop while preserving compatibility with older loops."""
        if self._callable_accepts_kw(self.agent_loop.run, "enable_caching"):
            return await self.agent_loop.run(
                task_text,
                enable_caching=enable_caching,
            )
        return await self.agent_loop.run(task_text)

    @staticmethod
    def _callable_accepts_kw(func, kwarg: str) -> bool:
        """Return whether *func* accepts *kwarg* or arbitrary keyword args."""
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return False
        return kwarg in signature.parameters or any(
            param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
        )

    def _caching_enabled(self, role: str = "engineering") -> bool:
        """
        Resolve the caching flag for this department call.

        Checks, in order:
        1. DepartmentConfig attached to the agent loop config by the orchestrator.
        2. DepartmentConfig attached directly to the agent loop for reviewer overrides.
        3. The agent loop's enable_caching flag.
        4. True, preserving the opt-out default for company orchestration.
        """
        loop_config = getattr(self.agent_loop, "config", None)
        dept_config = getattr(loop_config, "department_config", None)
        if role == "reviewer":
            dept_config = (
                getattr(self.agent_loop, "reviewer_department_config", None) or dept_config
            )
        if dept_config is not None:
            return bool(getattr(dept_config, "enable_caching", True))

        if self.config is not None:
            return self._get_caching_enabled()

        return bool(getattr(self.agent_loop, "enable_prompt_caching", True))

    async def request_spec_clarification(self, question: str) -> str:
        """Ask Product to clarify an ambiguous or incomplete PRD detail."""
        clarification_task = CompanyTask(
            task_id=uuid.uuid4().hex[:8],
            origin=self.name,
            target="product",
            artifact_type="memo",
            payload={"question": question},
            blocking=False,
        )
        if self._submit_task is not None:
            submitted = self._submit_task(clarification_task)
            if hasattr(submitted, "__await__"):
                await submitted
        else:
            raise RuntimeError("Department communication requires an orchestrator boundary")
        return f"Clarification request sent to Product: {question}"

    async def _emit_engineering_event(self, task_id: str, event_name: str, payload: dict) -> None:
        await self._emit_lifecycle_event(task_id, event_name, payload)
        emit = getattr(self.agent_loop, "_emit", None)
        if callable(emit):
            await emit(event_name, payload)

    async def _resolve_task(self, task_id: str) -> None:
        """Ensure _active_task is set for context building."""
        if self._active_task and self._active_task.task_id == task_id:
            return

        task_repository = getattr(self, "task_repository", None)
        if task_repository is not None:
            get_task = getattr(task_repository, "get", None)
            if callable(get_task):
                task = get_task(task_id)
                if hasattr(task, "__await__"):
                    task = await task
                if task is not None:
                    self._active_task = task
                    return

        # Best-effort fallback from project memory
        if self.memory and hasattr(self.memory, "get_active_project"):
            project = self.memory.get_active_project()
            if project and project.engineering_result:
                self._active_task = project.engineering_result

    def _review_context(self) -> dict:
        """Consolidated context for reviewer prompts."""
        task = self._active_task
        payload = task.payload if task and isinstance(task.payload, dict) else {}
        context = task.context if task and isinstance(getattr(task, "context", None), dict) else {}

        design_spec_structured = (
            context.get("design_spec_structured") or payload.get("design_spec_structured")
        )
        design_spec = context.get("design_spec") or payload.get("design_spec")

        return {
            "original_request": (
                context.get("original_request")
                or payload.get("original_request")
                or (task.payload if task and not isinstance(task.payload, dict) else "")
            ),
            "prd_content": context.get("prd_content") or payload.get("prd_content"),
            "prd_summary": self._get_prd_summary(),
            "design_spec": design_spec,
            "design_spec_summary": self._get_design_spec_summary(),
            "playbook_guidance": self._get_playbook_guidance(),
            "design_spec_structured": design_spec_structured,
        }

    def _get_design_spec_summary(self) -> str:
        """Rich DesignSpec summary for reviewer prompt."""
        task = self._active_task
        payload = task.payload if task and isinstance(task.payload, dict) else {}
        context = task.context if task and isinstance(getattr(task, "context", None), dict) else {}
        spec = (
            context.get("design_spec_structured")
            or payload.get("design_spec_structured")
            or context.get("design_spec")
            or payload.get("design_spec")
            or self.memory.data.get("design_spec")
        )
        if isinstance(spec, dict):
            parts = [f"Title: {spec.get('title', 'Untitled Design')}"]
            if overview := spec.get("overview"):
                parts.append(f"Overview: {str(overview)[:200]}")
            if screens := spec.get("key_screens"):
                parts.append(f"Key Screens: {len(screens)}")
            if components := spec.get("component_library"):
                parts.append(f"Components: {len(components)}")
            if accessibility := spec.get("accessibility_notes"):
                parts.append(f"Accessibility: {str(accessibility)[:150]}")
            return "\n".join(parts)
        return str(spec or "No DesignSpec available")[:800]

    @staticmethod
    def _summarize_design_spec(spec) -> str:
        if isinstance(spec, dict):
            parts = [f"Title: {spec.get('title', 'Untitled Design')}"]
            if overview := spec.get("overview"):
                parts.append(f"Overview: {str(overview)[:200]}")
            if screens := spec.get("key_screens"):
                parts.append(f"Key Screens: {len(screens)}")
            if components := spec.get("component_library"):
                parts.append(f"Components: {len(components)}")
            if accessibility := spec.get("accessibility_notes"):
                parts.append(f"Accessibility: {str(accessibility)[:150]}")
            return "\n".join(parts)
        return str(spec or "No DesignSpec available")[:800]

    def _get_reviewer_system_prompt(self, context) -> str:
        """Return high-quality reviewer prompt."""
        return f"""You are an expert Senior Software Engineer acting as a strict code reviewer.

Original PRD / Requirements:
{context.get('prd_summary', 'No PRD available')}

Design Spec (if any):
{context.get('design_spec_summary') or context.get('design_spec', 'No design spec')}

Coding Standards & Playbook:
{context.get('playbook_guidance', 'None')}

Review the following code changes carefully and be critical.

Changed Files:
{context.get('changed_files', [])}

Changed Files + Diffs:
{context.get('programmer_diffs', 'No diffs available')}

Targeted Check Results:
{context.get('targeted_checks', [])}

Evaluate:
- Correctness & functional requirements
- Edge cases and error handling
- Test coverage
- Security issues
- Code style and maintainability
- Adherence to PRD and design spec

Return only valid JSON with this exact shape:
{{
  "review_passed": true,
  "issues": [
    {{
      "file": "path/to/file.py",
      "line_range": "12-18",
      "severity": "critical|high|medium|low",
      "description": "specific problem",
      "suggestion": "specific fix"
    }}
  ],
  "overall_assessment": "concise assessment",
  "needs_revision": false
}}

Set needs_revision to true for any correctness, security, failing-check,
or spec-adherence issue that should block QA.
Be specific and actionable."""

    async def _run_structured_reviewer(self, review_context: dict):
        reviewer_model = self._reviewer_model()
        task = (
            "Perform a thorough code review of the recent changes. "
            "Do not edit files. Return the requested JSON only."
        )
        run_structured = getattr(self.agent_loop, "run_structured", None)
        if callable(run_structured):
            kwargs = {
                "task": task,
                "system_prompt": self._get_reviewer_system_prompt(review_context),
                "model": reviewer_model,
            }
            if self._callable_accepts_kw(run_structured, "enable_caching"):
                kwargs["enable_caching"] = self._caching_enabled(role="reviewer")
            return await run_structured(**kwargs)

        coder = getattr(self.agent_loop, "architect_coder", None) or getattr(
            self.agent_loop, "coder", None
        )
        if coder is None:
            raise RuntimeError("Engineering reviewer requires an agent loop or coder.")

        prompt = f"{self._get_reviewer_system_prompt(review_context)}\n\n" f"Reviewer task:\n{task}"
        run_structured_async = getattr(coder, "run_structured_async", None)
        if callable(run_structured_async):
            return await run_structured_async(prompt, preproc=True, include_diff=False)
        run_async = getattr(coder, "run_async", None)
        if callable(run_async):
            return await run_async(prompt, preproc=True)
        raise RuntimeError("Engineering reviewer coder does not support structured execution.")

    def _reviewer_model(self) -> str:
        config = getattr(self.agent_loop, "config", None)
        return (
            getattr(config, "reviewer_model", None)
            or getattr(config, "architect_model", None)
            or "claude-3-7-sonnet-20250219"
        )

    def _parse_reviewer_output(self, result) -> dict:
        raw = self._result_content(result)
        if not raw and hasattr(result, "to_dict"):
            raw = str(result.to_dict())
        data = result if isinstance(result, dict) else None
        if data and "content" in data and isinstance(data["content"], dict):
            data = data["content"]
        elif not self._looks_like_review_data(data):
            data = self._extract_review_json(raw)

        if not isinstance(data, dict):
            logger.warning(
                "Failed to parse reviewer JSON; treating output as a blocking review issue."
            )
            return {
                "review_passed": False,
                "issues": [
                    {
                        "file": None,
                        "line_range": None,
                        "severity": "high",
                        "description": "Reviewer output was malformed",
                        "suggestion": f"Raw output: {str(raw or result)[:500]}",
                    }
                ],
                "overall_assessment": "Review failed due to malformed output",
                "needs_revision": True,
            }

        for field in ("review_passed", "issues", "overall_assessment", "needs_revision"):
            if field not in data:
                logger.warning("Reviewer output missing required field: %s", field)

        issues = self._normalize_review_issues(data.get("issues"))
        needs_revision = self._coerce_bool(data.get("needs_revision"), bool(issues))
        review_passed = self._coerce_bool(
            data.get("review_passed"), not needs_revision and not issues
        )
        if needs_revision:
            review_passed = False
        elif issues and review_passed:
            needs_revision = True
            review_passed = False

        overall_assessment = str(
            data.get("overall_assessment")
            or data.get("summary")
            or raw
            or ("Review passed." if review_passed else "Review needs revision.")
        ).strip()

        return {
            "review_passed": review_passed,
            "issues": issues,
            "overall_assessment": overall_assessment,
            "needs_revision": needs_revision,
        }

    @staticmethod
    def _looks_like_review_data(data) -> bool:
        return isinstance(data, dict) and any(
            key in data
            for key in (
                "review_passed",
                "issues",
                "overall_assessment",
                "needs_revision",
            )
        )

    @staticmethod
    def _extract_review_json(raw: str):
        if not raw:
            return None
        text = str(raw).strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        candidates = [fence.group(1)] if fence else []
        candidates.append(text)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(text[start : end + 1])

        seen = set()
        for candidate in candidates:
            for normalized in (
                candidate,
                EngineeringDepartment._sanitize_reviewer_json(candidate),
            ):
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                try:
                    parsed = json.loads(normalized)
                except json.JSONDecodeError as err:
                    logger.warning("Failed to parse reviewer JSON candidate: %s", err)
                    continue
                if isinstance(parsed, dict):
                    return parsed
        return None

    @staticmethod
    def _sanitize_reviewer_json(candidate: str) -> str:
        """Remove common LLM JSON artifacts without changing valid JSON."""
        text = str(candidate).strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE)
        text = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r",(\s*[}\]])", r"\1", text)
        return text.strip()

    @staticmethod
    def _normalize_review_issues(issues) -> list[dict]:
        if not isinstance(issues, list):
            return []
        normalized = []
        for issue in issues:
            if isinstance(issue, str):
                normalized.append(
                    {
                        "file": None,
                        "line_range": None,
                        "severity": "medium",
                        "description": issue,
                        "suggestion": "Investigate and address this reviewer concern.",
                    }
                )
                continue
            if not isinstance(issue, dict):
                continue
            normalized.append(
                {
                    "file": issue.get("file"),
                    "line_range": (
                        issue.get("line_range") or issue.get("line") or issue.get("lines")
                    ),
                    "severity": str(issue.get("severity") or "medium").lower(),
                    "description": str(
                        issue.get("description")
                        or issue.get("issue")
                        or issue.get("problem")
                        or "Reviewer issue"
                    ),
                    "suggestion": str(
                        issue.get("suggestion")
                        or issue.get("action")
                        or issue.get("fix")
                        or "Address the issue."
                    ),
                }
            )
        return normalized

    @staticmethod
    def _coerce_bool(value, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1", "passed", "pass"}
        if value is None:
            return default
        return bool(value)

    def _get_prd_summary(self) -> str:
        prd = self._get_prd_context(self._active_task) if self._active_task else None
        prd = prd or self.memory.data.get("prd")
        return str(prd or "No PRD available")

    def _get_design_spec(self) -> str:
        design_spec = self._get_design_context(self._active_task) if self._active_task else None
        design_spec = design_spec or self.memory.data.get("design_spec")
        return str(design_spec or "No design spec")

    def _get_playbook_guidance(self) -> str:
        """Extract relevant playbook patterns for reviewer."""
        guidance = None
        if self._active_task:
            payload = self._active_task.payload if isinstance(self._active_task.payload, dict) else {}
            context = (
                self._active_task.context
                if isinstance(getattr(self._active_task, "context", None), dict)
                else {}
            )
            guidance = context.get("playbook_guidance") or payload.get("playbook_guidance")

        if not guidance:
            playbook = self.memory.data.get("playbook", {})
            guidance = []
            if isinstance(playbook, dict):
                for values in playbook.values():
                    if isinstance(values, list):
                        guidance.extend(values)

        if not guidance:
            return "No playbook guidance available"

        items = guidance if isinstance(guidance, list) else [guidance]
        formatted = []
        for index, item in enumerate(items[:5], 1):
            if isinstance(item, dict):
                pattern = (
                    item.get("pattern")
                    or item.get("title")
                    or item.get("text")
                    or "Unknown pattern"
                )
                context_text = item.get("context") or item.get("rationale") or ""
                suffix = f": {str(context_text)[:200]}" if context_text else ""
                formatted.append(f"{index}. {pattern}{suffix}")
            else:
                formatted.append(f"{index}. {str(item)[:200]}")
        return "\n".join(formatted)

    def _record_reviewer_metrics(
        self, review_data: dict, review_iteration: Optional[int] = None
    ) -> None:
        """Track reviewer performance and learn repeated feedback patterns."""
        observability = self.memory.data.setdefault("observability", {})
        stats = observability.setdefault(
            "reviewer_stats",
            {
                "total_reviews": 0,
                "approved_reviews": 0,
                "approval_rate": 0.0,
                "avg_issues_per_review": 0.0,
                "total_issues": 0,
                "issue_type_counts": {},
                "most_common_issues": [],
            },
        )
        stats["total_reviews"] = int(stats.get("total_reviews", 0) or 0) + 1
        if review_iteration is not None:
            stats["last_review_iteration"] = review_iteration
        if review_data.get("review_passed"):
            stats["approved_reviews"] = int(stats.get("approved_reviews", 0) or 0) + 1
        issues = review_data.get("issues") or []
        stats["total_issues"] = int(stats.get("total_issues", 0) or 0) + len(issues)
        total_reviews = max(1, int(stats["total_reviews"]))
        stats["approval_rate"] = round(
            int(stats.get("approved_reviews", 0) or 0) / total_reviews, 2
        )
        stats["avg_issues_per_review"] = round(
            int(stats.get("total_issues", 0) or 0) / total_reviews, 2
        )

        issue_type_counts = stats.setdefault("issue_type_counts", {})
        for issue in issues:
            issue_type = self._review_issue_type(issue)
            issue_type_counts[issue_type] = int(issue_type_counts.get(issue_type, 0) or 0) + 1
        stats["most_common_issues"] = [
            key
            for key, _ in sorted(issue_type_counts.items(), key=lambda item: item[1], reverse=True)[
                :5
            ]
        ]
        self.memory.persist()

    @staticmethod
    def _review_issue_type(issue: dict) -> str:
        description = str(issue.get("description") or "").lower()
        suggestion = str(issue.get("suggestion") or "").lower()
        text = f"{description} {suggestion}"
        if "test" in text or "assert" in text:
            return "missing_tests"
        if "accessibility" in text or "wcag" in text or "aria" in text:
            return "accessibility"
        if "error" in text or "exception" in text or "try" in text:
            return "error_handling"
        if "security" in text or "injection" in text or "auth" in text:
            return "security"
        if "malformed" in text or "json" in text:
            return "malformed_reviewer_output"
        return "general"

    def _learn_reviewer_playbook_pattern(self, review_data: dict, task_id: str) -> None:
        """Only learn systemic patterns that appear across multiple tasks."""
        issues = review_data.get("issues", [])
        if not issues:
            return

        extractor = AuditPatternExtractor(self._get_audit_log())
        for issue in issues[:5]:  # limit per review
            issue_type = self._review_issue_type(issue)
            if not issue_type:
                continue

            # Require evidence from at least 3 distinct tasks before playbook entry.
            if extractor.count_occurrences(issue_type, min_unique_tasks=3) >= 2:
                self._append_reviewer_playbook_entry(
                    issue_type,
                    issue,
                    metadata={"task_id": task_id},
                )

    def _get_audit_log(self) -> list[dict]:
        get_audit_log = getattr(self.memory, "get_audit_log", None)
        if callable(get_audit_log):
            records = get_audit_log()
        else:
            records = self.memory.data.get("audit_log", [])
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]

    def _append_reviewer_playbook_entry(
        self, issue_type: str, issue: dict, metadata: Optional[dict] = None
    ) -> None:
        entry = f"Recurring issue type '{issue_type}': {issue.get('description')}"
        entry_metadata = {
            "severity": issue.get("severity"),
            "suggestion": issue.get("suggestion"),
        }
        if metadata:
            entry_metadata.update(metadata)

        append_playbook_entry = getattr(self.memory, "append_playbook_entry", None)
        if callable(append_playbook_entry):
            append_playbook_entry(
                category="code_review",
                entry=entry,
                metadata=entry_metadata,
            )
            return

        playbook = self.memory.data.setdefault("playbook", {})
        entries = playbook.setdefault("code_review", [])
        existing_texts = [
            existing.get("text") if isinstance(existing, dict) else str(existing)
            for existing in entries
        ]
        if entry not in existing_texts:
            entries.append(
                {
                    "text": entry,
                    "pattern": entry,
                    "context": f"Reviewer repeatedly flagged {issue_type.replace('_', ' ')}.",
                    "source": "reviewer_feedback",
                    "metadata": entry_metadata,
                }
            )
            self.memory.persist()

    @staticmethod
    def _reviewer_prompt(context: dict) -> str:
        return (
            "You are the Engineering Reviewer. Use a strong review model "
            "(Claude 3.7 Sonnet, GPT-5.5, or equivalent) when configured. "
            "Compare the implementation diff and changed files against the PRD, "
            "design spec, current playbook items, and coding standards. Return "
            "structured feedback with positives, required fixes, priorities, and "
            "an approval decision."
        )

    def _build_review_feedback(
        self,
        *,
        previous_deliverable: Deliverable,
        changed_files: list[str],
        diff: str,
        checks: list[dict],
        context: dict,
    ) -> dict:
        positives = []
        concerns = []
        priority_issues = []

        if changed_files:
            positives.append(
                f"Implementation updated {len(changed_files)} file(s): "
                + ", ".join(changed_files[:8])
            )
        else:
            concerns.append("No changed files were detected for review.")

        if diff:
            positives.append("Reviewer received an implementation diff for inspection.")
        elif changed_files:
            concerns.append("Changed files were found, but no diff was available.")

        if context.get("prd_content"):
            positives.append("PRD context was available to compare scope and behavior.")
        else:
            concerns.append("No PRD context was supplied to the reviewer.")

        if context.get("design_spec"):
            positives.append("Design specification context was available.")

        if context.get("playbook_guidance"):
            positives.append("Current playbook guidance was included in the review.")

        if previous_deliverable.status == "failure":
            priority_issues.append(
                {
                    "priority": "P0",
                    "issue": "Programmer phase reported a failure.",
                    "action": "Fix the implementation error before QA handoff.",
                }
            )

        for check in checks:
            if check.get("status") == "failed":
                priority_issues.append(
                    {
                        "priority": "P0" if check.get("required", True) else "P1",
                        "issue": f"Reviewer check failed: {check.get('name')}",
                        "action": check.get("output") or "Investigate and fix the failed check.",
                    }
                )
            elif check.get("status") == "skipped":
                concerns.append(
                    f"Skipped {check.get('name')}: {check.get('reason', 'not available')}"
                )

        return {
            "summary": "Approved for QA." if not priority_issues else "Needs revision before QA.",
            "what_is_good": positives,
            "concerns": concerns,
            "priority_issues": priority_issues,
            "changed_files": changed_files,
            "checks": checks,
        }

    async def _changed_files(self, metadata: dict) -> list[str]:
        files = metadata.get("files") or metadata.get("files_changed") or []
        if isinstance(files, str):
            files = [files]
        files = [str(path) for path in files if path]
        if files:
            return sorted(dict.fromkeys(files))

        return await self._git_changed_files()

    async def _git_changed_files(self) -> list[str]:
        root = self._repo_root()
        if root is None:
            return []
        result = await self._run_command(["git", "status", "--short"], root)
        files = []
        for line in result.get("stdout", "").splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path)
        return sorted(dict.fromkeys(files))

    async def _implementation_diff(self, metadata: dict) -> str:
        """Return diff with strong truncation warning."""
        diff = metadata.get("diffs_summary") or await self._raw_git_diff()
        lines = str(diff).splitlines()
        if len(lines) > 500:
            diff = "\n".join(lines[:500])
            diff += "\n\n[WARNING: Diff truncated to first 500 lines for reviewer. "
            diff += "Do not assume untouched sections are correct. "
            diff += "Focus only on the provided changes for consistency checks.]\n"
        return str(diff)

    async def _raw_git_diff(self) -> str:
        """Return the current repository diff for reviewer context."""
        root = self._repo_root()
        if root is None:
            return "Diff unavailable"
        result = await self._run_command(["git", "diff", "--stat"], root)
        stat = result.get("stdout", "")
        result = await self._run_command(["git", "diff", "--"], root)
        body = result.get("stdout", "")
        return "\n".join(part for part in (stat, body) if part).strip()

    async def _run_targeted_checks(self, changed_files: list[str]) -> list[dict]:
        root = self._repo_root()
        if root is None:
            return [
                {
                    "name": "targeted_checks",
                    "status": "skipped",
                    "reason": "No git repository root is available.",
                    "required": False,
                }
            ]

        checks = []
        diff_check = await self._run_command(["git", "diff", "--check"], root)
        checks.append(self._check_result("git diff --check", diff_check, required=True))

        python_files = [path for path in changed_files if path.endswith(".py")]
        existing_python_files = [path for path in python_files if (root / path).exists()]
        if existing_python_files:
            command = ["python", "-m", "py_compile", *existing_python_files]
            py_compile = await self._run_command(command, root)
            checks.append(
                self._check_result(
                    "python -m py_compile "
                    + " ".join(shlex.quote(path) for path in existing_python_files),
                    py_compile,
                    required=True,
                )
            )
        else:
            checks.append(
                {
                    "name": "python -m py_compile",
                    "status": "skipped",
                    "reason": "No changed Python files detected.",
                    "required": False,
                }
            )
        return checks

    @staticmethod
    def _check_result(name: str, result: dict, *, required: bool) -> dict:
        output = (result.get("stdout", "") + result.get("stderr", "")).strip()
        return {
            "name": name,
            "status": "passed" if result.get("returncode") == 0 else "failed",
            "returncode": result.get("returncode"),
            "output": output[-4000:],
            "required": required,
        }

    async def _run_command(self, command: list[str], cwd: Path) -> dict:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
        }

    def _repo_root(self) -> Optional[Path]:
        coder = getattr(self.agent_loop, "coder", None)
        repo = getattr(coder, "repo", None)
        root = getattr(repo, "root", None)
        if root:
            return Path(root)
        root = getattr(coder, "root", None)
        if root:
            return Path(root)
        repo_path = getattr(self.memory, "repo_path", None)
        if repo_path:
            return Path(repo_path)
        return None

    @staticmethod
    def _deliverable_needs_revision(deliverable: Optional[Deliverable]) -> bool:
        if deliverable is None:
            return False
        return bool(
            deliverable.metadata.get("needs_revision")
            or deliverable.status == "needs_revision"
            or deliverable.review_passed is False
        )

    @staticmethod
    def _feedback_data(feedback: Optional[Deliverable | dict]) -> dict:
        if feedback is None:
            return {}
        if isinstance(feedback, Deliverable):
            return (
                feedback.review_feedback
                or feedback.metadata.get("review_feedback")
                or feedback.metadata
                or {}
            )
        return feedback

    def _feedback_issues(self, feedback: Optional[Deliverable | dict]) -> list:
        data = self._feedback_data(feedback)
        return data.get("issues") or []

    def _review_feedback_summary(self, feedback: Optional[Deliverable | dict]) -> Optional[str]:
        if not feedback:
            return None
        data = self._feedback_data(feedback)
        issues = data.get("issues") or []
        if issues:
            assessment = str(data.get("overall_assessment") or data.get("summary") or "")
            return (f"{len(issues)} issues found. " + assessment[:150]).strip()

        priority_issues = data.get("priority_issues") or []
        if priority_issues:
            summaries = []
            for issue in priority_issues[:5]:
                if isinstance(issue, dict):
                    priority = issue.get("priority")
                    description = issue.get("issue") or issue.get("name")
                    action = issue.get("action")
                    if description:
                        prefix = f"[{priority}] " if priority else ""
                        summary = f"{prefix}{description}"
                        if action:
                            summary += f" — {action}"
                        summaries.append(summary)
                elif issue:
                    summaries.append(str(issue))
            if summaries:
                remaining = len(priority_issues) - len(summaries)
                summary = "; ".join(summaries)
                if remaining > 0:
                    summary += f"; +{remaining} more"
                return summary

        summary = data.get("overall_assessment") or data.get("summary")
        return str(summary) if summary else None

    def _format_review_feedback(self, feedback: Deliverable | dict) -> str:
        """Format reviewer feedback for the programmer prompt."""
        data = self._feedback_data(feedback)
        if not data:
            return "No reviewer feedback was provided."

        issues = data.get("issues") or []
        if issues:
            formatted = []
            for issue in issues:
                if not isinstance(issue, dict):
                    formatted.append(f"[MEDIUM] general: {issue}")
                    continue
                severity = str(issue.get("severity") or "medium").upper()
                file = issue.get("file") or "unknown"
                line_range = issue.get("line_range") or issue.get("line") or ""
                if line_range and not str(line_range).startswith(":"):
                    line_range = f":{line_range}"
                desc = issue.get("description") or issue.get("issue") or ""
                sugg = issue.get("suggestion") or issue.get("action") or ""
                line = f"[{severity}] {file}{line_range}: {desc}"
                if sugg:
                    line += f"\n   → {sugg}"
                formatted.append(line)
            return "\n".join(formatted)

        lines = [str(data.get("overall_assessment") or data.get("summary") or "Reviewer feedback")]
        for key, label in (
            ("priority_issues", "Priority issues"),
            ("concerns", "Concerns"),
            ("what_is_good", "What is good"),
        ):
            values = data.get(key) or []
            if not values:
                continue
            lines.append(f"\n{label}:")
            for value in values:
                if isinstance(value, dict):
                    issue = value.get("issue") or value.get("name") or value
                    action = value.get("action")
                    priority = value.get("priority")
                    prefix = f"[{priority}] " if priority else ""
                    line = f"- {prefix}{issue}"
                    if action:
                        line += f" Action: {action}"
                    lines.append(line)
                else:
                    lines.append(f"- {value}")
        return "\n".join(lines)

    def _uses_agent_conversation_memory(self) -> bool:
        coder = getattr(self.agent_loop, "coder", None)
        return self.conversation is getattr(coder, "conversation_memory", None)

    @staticmethod
    def _task_text(task: CompanyTask) -> str:
        if not isinstance(task.payload, dict):
            return str(task.payload)

        parts = []
        original_request = task.payload.get("original_request")
        prd_content = task.payload.get("prd_content")
        clarification_response = task.payload.get("clarification_response")
        design_spec = task.payload.get("design_spec")
        qa_report = task.payload.get("qa_report")
        deploy_report = task.payload.get("deploy_report")
        ceo_feedback = task.payload.get("ceo_feedback")
        instruction = task.payload.get("instruction")
        review_feedback = task.payload.get("review_feedback")
        playbook_guidance = (
            task.context.get("playbook_guidance") if isinstance(task.context, dict) else None
        )
        if original_request:
            parts.append(f"Original request:\n{original_request}")
        if prd_content:
            parts.append(f"PRD content:\n{prd_content}")
        if clarification_response:
            parts.append(f"Product clarification:\n{clarification_response}")
        if design_spec:
            parts.append(f"UX design spec:\n{design_spec}")
        if qa_report:
            parts.append(f"QA feedback:\n{qa_report}")
        if deploy_report:
            parts.append(f"DevOps deploy report:\n{deploy_report}")
        if ceo_feedback:
            parts.append(f"CEO feedback:\n{ceo_feedback}")
        if instruction:
            parts.append(f"Instruction:\n{instruction}")
        if review_feedback:
            parts.append(f"Review feedback:\n{review_feedback}")
        if playbook_guidance:
            parts.append(
                "Project playbook guidance:\n"
                + "\n".join(f"- {item}" for item in playbook_guidance)
            )
        if not parts:
            parts.append(str(task.payload))
        return "\n\n".join(parts)

    @staticmethod
    def _result_content(result) -> str:
        if isinstance(result, dict):
            coder_result = result.get("coder_result") or {}
            return (
                result.get("content") or result.get("summary") or coder_result.get("summary") or ""
            )
        return getattr(result, "content", None) or getattr(result, "summary", "") or ""

    @staticmethod
    def _result_error(result):
        if isinstance(result, dict):
            return result.get("error")
        return getattr(result, "error", None)

    @staticmethod
    def _result_metadata(result) -> dict:
        if isinstance(result, dict):
            coder_result = result.get("coder_result") or {}
            metadata = dict(result.get("metadata") or {})
            files = (
                result.get("files")
                or result.get("files_changed")
                or coder_result.get("files_changed")
                or metadata.get("files")
                or metadata.get("files_changed")
                or []
            )
            commits = result.get("commits") or metadata.get("commits") or []
            commit_hash = coder_result.get("commit_hash")
            if commit_hash and commit_hash not in commits:
                commits = [*commits, commit_hash]
            diffs = result.get("diffs") or metadata.get("diffs") or []
            diff = coder_result.get("diff")
            if diff and diff not in diffs:
                diffs = [*diffs, diff]
            metadata.update({"files": files, "commits": commits, "diffs": diffs})
            return metadata

        files = getattr(result, "files", None) or getattr(result, "files_changed", []) or []
        commits = getattr(result, "commits", []) or []
        commit_hash = getattr(result, "commit_hash", None)
        if commit_hash and commit_hash not in commits:
            commits = [*commits, commit_hash]
        diffs = getattr(result, "diffs", []) or []
        diff = getattr(result, "diff", None)
        if diff and diff not in diffs:
            diffs = [*diffs, diff]
        return {"files": files, "commits": commits, "diffs": diffs}
