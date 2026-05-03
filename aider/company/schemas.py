from dataclasses import dataclass, field
from typing import Literal, Optional, Any


@dataclass
class CompanyTask:
    task_id: str
    origin: str           # e.g. "ceo", "product"
    target: str           # department name
    artifact_type: Literal["raw_prompt", "prd", "design_spec", "code", "test_report", "general"]
    payload: str
    blocking: bool = False
    context: dict = field(default_factory=dict)


@dataclass
class Deliverable:
    task_id: str
    department: str
    artifact_type: str
    payload: str
    status: Literal["success", "failure", "needs_review"]
    metadata: dict = field(default_factory=dict)
