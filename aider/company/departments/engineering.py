from typing import Optional

from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable
from aider.agent.loop import AiderAgentLoop
from aider.memory import ProjectMemory, ConversationMemory, Message


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
        # 1. Append user turn to departmental conversation memory
        self.conversation.add(Message(role="user", content=task.payload))

        # 2. Build AgentContext using YOUR existing logic pattern
        #    (Keep this identical to how your Discord bot builds it today)
        context = self.agent_loop.build_context(
            conversation_buffer=list(self.conversation.messages),
            project_memory=self.memory,
        )

        # 3. Run your proven Aider loop (Architect → Editor)
        result = await self.agent_loop.run(context)

        # 4. Capture assistant output back into memory
        if result.content:
            self.conversation.add(Message(role="assistant", content=result.content))

        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="code",
            payload=result.content or "",
            status="success" if not getattr(result, "error", None) else "failure",
            metadata={
                "files": getattr(result, "files", []),
                "commits": getattr(result, "commits", []),
                "diffs": getattr(result, "diffs", []),
            }
        )
