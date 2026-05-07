import asyncio
import shlex
import uuid
from pathlib import Path
from typing import Optional

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable
from aider.agent.loop import AiderAgentLoop
from aider.memory import ProjectMemory, ConversationMemory


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
    ):
        super().__init__(project_memory, conversation_memory)
        self.agent_loop = agent_loop
        self.tools = ["aider_coder"]
        self.current_stage: str = "programmer"
        self._active_task: Optional[CompanyTask] = None
        self._review_feedback: Optional[dict] = None
        if hasattr(self.agent_loop, "tool_registry"):
            self.agent_loop.tool_registry.set_department(self)

    async def process(self, task: CompanyTask) -> Deliverable:
        self._active_task = task
        self._review_feedback = None
        last_programmer_deliverable: Optional[Deliverable] = None
        last_review: Optional[Deliverable] = None

        for iteration in range(1, self.max_internal_iterations + 1):
            await self._emit_engineering_event(
                task.task_id,
                "engineering_programmer_start",
                {"iteration": iteration, "max_iterations": self.max_internal_iterations},
            )
            programmer_deliverable = await self._run_programmer_phase(task)
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
            self._review_feedback = review.review_feedback or review.metadata.get(
                "review_feedback"
            )

            if review.review_passed:
                await self._emit_engineering_event(
                    task.task_id,
                    "engineering_review_approved",
                    {
                        "iteration": iteration,
                        "files": review.metadata.get("files", []),
                        "checks": review.metadata.get("review_checks", []),
                    },
                )
                return review

            await self._emit_engineering_event(
                task.task_id,
                "engineering_revision_needed",
                {
                    "iteration": iteration,
                    "feedback": self._review_feedback,
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
                metadata={"review_passed": False},
                review_passed=False,
            )

        metadata = dict(fallback.metadata)
        metadata.update(
            {
                "review_passed": False,
                "review_feedback": self._review_feedback,
                "max_internal_iterations": self.max_internal_iterations,
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

    async def _run_programmer_phase(self, task: CompanyTask) -> Deliverable:
        """Run the existing Architect → Editor implementation flow."""
        self.current_stage = "programmer"
        task_text = self._task_text(task)
        if self._review_feedback:
            task_text = (
                f"{task_text}\n\n"
                "Reviewer requested revisions. Address every item below before "
                "returning the implementation:\n"
                f"{self._format_review_feedback(self._review_feedback)}"
            )

        record_department_memory = not self._uses_agent_conversation_memory()
        if record_department_memory:
            self.conversation.add(role="user", content=task_text)

        result = await self.agent_loop.run(task_text)

        content = self._result_content(result)
        if content and record_department_memory:
            self.conversation.add(role="assistant", content=content)

        metadata = self._result_metadata(result)
        metadata.setdefault("stage", "programmer")
        metadata.setdefault("review_feedback_applied", self._review_feedback)
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="code",
            payload=content,
            status="failure" if self._result_error(result) else "success",
            metadata=metadata,
        )

    async def _run_reviewer_phase(
        self, previous_deliverable: Deliverable
    ) -> Deliverable:
        """Review implementation diffs, context, standards, and targeted checks."""
        self.current_stage = "reviewer"
        task = self._active_task
        metadata = dict(previous_deliverable.metadata)
        changed_files = await self._changed_files(metadata)
        diff = await self._implementation_diff(metadata)
        checks = await self._run_targeted_checks(changed_files)
        context = self._review_context(task)
        feedback = self._build_review_feedback(
            previous_deliverable=previous_deliverable,
            changed_files=changed_files,
            diff=diff,
            checks=checks,
            context=context,
        )
        review_passed = not feedback["priority_issues"]
        status = "success" if review_passed else "needs_revision"
        metadata.update(
            {
                "stage": "reviewer",
                "files": changed_files,
                "diffs": [diff] if diff else metadata.get("diffs", []),
                "review_prompt": self._reviewer_prompt(context),
                "review_feedback": feedback,
                "review_passed": review_passed,
                "review_checks": checks,
            }
        )
        payload = previous_deliverable.payload
        if not review_passed:
            payload = (
                f"{previous_deliverable.payload}\n\n"
                "Reviewer requested revisions:\n"
                f"{self._format_review_feedback(feedback)}"
            )

        return Deliverable(
            task_id=previous_deliverable.task_id,
            department=self.name,
            artifact_type="code",
            payload=payload,
            status=status,
            metadata=metadata,
            review_feedback=feedback,
            review_passed=review_passed,
        )

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
            raise RuntimeError(
                "Department communication requires an orchestrator boundary"
            )
        return f"Clarification request sent to Product: {question}"

    async def _emit_engineering_event(
        self, task_id: str, event_name: str, payload: dict
    ) -> None:
        await self._emit_lifecycle_event(task_id, event_name, payload)
        emit = getattr(self.agent_loop, "_emit", None)
        if callable(emit):
            await emit(event_name, payload)

    def _review_context(self, task: Optional[CompanyTask]) -> dict:
        payload = task.payload if task and isinstance(task.payload, dict) else {}
        task_context = task.context if task and isinstance(task.context, dict) else {}
        return {
            "original_request": payload.get("original_request") or (task.payload if task else ""),
            "prd_content": payload.get("prd_content") or task_context.get("prd_content"),
            "design_spec": payload.get("design_spec") or task_context.get("design_spec"),
            "playbook_guidance": task_context.get("playbook_guidance")
            or payload.get("playbook_guidance")
            or [],
        }

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
        diffs = metadata.get("diffs") or []
        if isinstance(diffs, str):
            diffs = [diffs]
        diff = "\n".join(str(item) for item in diffs if item)
        if diff:
            return diff

        root = self._repo_root()
        if root is None:
            return ""
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
    def _format_review_feedback(feedback: dict) -> str:
        if not feedback:
            return "No reviewer feedback was provided."
        lines = [str(feedback.get("summary") or "Reviewer feedback")]
        for key, label in (
            ("priority_issues", "Priority issues"),
            ("concerns", "Concerns"),
            ("what_is_good", "What is good"),
        ):
            values = feedback.get(key) or []
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
            task.context.get("playbook_guidance")
            if isinstance(task.context, dict)
            else None
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
                result.get("content")
                or result.get("summary")
                or coder_result.get("summary")
                or ""
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

        files = (
            getattr(result, "files", None) or getattr(result, "files_changed", []) or []
        )
        commits = getattr(result, "commits", []) or []
        commit_hash = getattr(result, "commit_hash", None)
        if commit_hash and commit_hash not in commits:
            commits = [*commits, commit_hash]
        diffs = getattr(result, "diffs", []) or []
        diff = getattr(result, "diff", None)
        if diff and diff not in diffs:
            diffs = [*diffs, diff]
        return {"files": files, "commits": commits, "diffs": diffs}
