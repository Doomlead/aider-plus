from typing import Optional

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable
from aider.agent.loop import AiderAgentLoop
from aider.memory import ProjectMemory, ConversationMemory


class EngineeringDepartment(Department):
    name = "engineering"

    def __init__(
        self,
        project_memory: ProjectMemory,
        agent_loop: AiderAgentLoop,
        conversation_memory: Optional[ConversationMemory] = None,
    ):
        super().__init__(project_memory, conversation_memory)
        self.agent_loop = agent_loop
        self.tools = ["aider_coder"]

    async def process(self, task: CompanyTask) -> Deliverable:
        # Keep the agent loop self-contained: it builds context from its own
        # coder-backed conversation/project memory when run with raw task text.
        record_department_memory = not self._uses_agent_conversation_memory()
        if record_department_memory:
            self.conversation.add(role="user", content=task.payload)

        result = await self.agent_loop.run(task.payload)

        content = self._result_content(result)
        if content and record_department_memory:
            self.conversation.add(role="assistant", content=content)

        metadata = self._result_metadata(result)
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="code",
            payload=content,
            status="failure" if self._result_error(result) else "success",
            metadata=metadata,
        )

    def _uses_agent_conversation_memory(self) -> bool:
        coder = getattr(self.agent_loop, "coder", None)
        return self.conversation is getattr(coder, "conversation_memory", None)

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
            files = (
                result.get("files")
                or result.get("files_changed")
                or coder_result.get("files_changed")
                or []
            )
            commits = result.get("commits") or []
            commit_hash = coder_result.get("commit_hash")
            if commit_hash and commit_hash not in commits:
                commits = [*commits, commit_hash]
            diffs = result.get("diffs") or []
            diff = coder_result.get("diff")
            if diff and diff not in diffs:
                diffs = [*diffs, diff]
            return {"files": files, "commits": commits, "diffs": diffs}

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
