from dataclasses import dataclass
from typing import Literal, Optional

from aider.company.schemas import Deliverable


@dataclass
class Project:
    project_id: str
    name: str
    phase: Literal[
        "prototyping",
        "design",
        "development",
        "qa",
        "release_ready",
        "deploying",
        "done",
    ] = "prototyping"
    prd: Optional[str] = None
    requires_design: bool = False
    design_spec: Optional[dict] = None
    engineering_result: Optional[Deliverable] = None
    qa_result: Optional[Deliverable] = None
    deploy_result: Optional[Deliverable] = None
    revision_count: int = 0
