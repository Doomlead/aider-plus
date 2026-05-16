from dataclasses import dataclass
from typing import Literal, Optional

from aider.company.schemas import Deliverable, ProjectPlan


@dataclass
class Project:
    project_id: str
    name: str
    phase: Literal[
        "prototyping",
        "design",
        "development",
        "qa",
        "delivery",
        "release_ready",
        "deploying",
        "post_mortem",
        "done",
    ] = "prototyping"
    prd: Optional[str] = None
    requires_design: bool = False
    design_spec: Optional[dict] = None
    engineering_result: Optional[Deliverable] = None
    qa_result: Optional[Deliverable] = None
    delivery_result: Optional[Deliverable] = None
    delivery_plan: Optional[ProjectPlan] = None
    deploy_result: Optional[Deliverable] = None
    revision_count: int = 0
