from dataclasses import dataclass
from typing import Literal, Optional

from aider.company.schemas import Deliverable


@dataclass
class Project:
    project_id: str
    name: str
    phase: Literal["prototyping", "development", "qa", "release_ready", "done"] = "prototyping"
    prd: Optional[str] = None
    engineering_result: Optional[Deliverable] = None
    qa_result: Optional[Deliverable] = None
    revision_count: int = 0
