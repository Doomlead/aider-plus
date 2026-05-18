from __future__ import annotations

import json
from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.departments.security_app import (
    _parse_json,
    _result_from_dict,
    _scan_type,
    _status_for_severity,
    _critical_findings,
)
from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable, SecurityScanResult
from aider.memory import ConversationMemory, ProjectMemory

_PLATFORMSEC_SYSTEM = """You are the PlatformSec Department for an AI company.
Focus on the company platform itself: agent isolation, prompt-injection defenses,
tool policy enforcement, audit hardening, MCP/tool sandboxing, secrets handling,
and daemon security. Return strict JSON with scan_type=platform_audit, severity,
findings [{location, description, recommendation, cve?}], fixed_count,
risk_score, and raw_output_summary."""


class PlatformSecDepartment(Department):
    """Platform Security department for AI-company infrastructure controls."""

    name = "security_platform"
    allowed_tools = ["file_read", "repo_search", "audit_read"]

    def __init__(
        self,
        project_memory: ProjectMemory,
        agent_loop: AiderAgentLoop,
        conversation_memory: Optional[ConversationMemory] = None,
        config: Optional[DepartmentConfig] = None,
    ):
        super().__init__(project_memory, conversation_memory, config=config)
        self.agent_loop = agent_loop
        self.tools = ["file_read", "repo_search", "audit_read"]
        if hasattr(self.agent_loop, "tool_registry"):
            self.agent_loop.tool_registry.set_department(self)

    def get_context_requirements(self) -> list[str]:
        return [
            "playbook.*",
            "skills.shared",
            "skills.security_platform",
        ]

    async def process(self, task: CompanyTask) -> Deliverable:
        result = await self._run_audit(task)
        status = (
            "needs_review" if result.severity in {"critical", "high"} else "success"
        )
        metadata = {
            "security_status": _status_for_severity(result.severity),
            "security_scan_result": result.to_dict(),
            "finding_count": len(result.findings),
            "critical_findings": _critical_findings(result),
            "context": dict(task.context or {}),
        }
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="security_scan_result",
            payload=result.to_dict(),
            status=status,
            metadata=metadata,
        )

    async def _run_audit(self, task: CompanyTask) -> SecurityScanResult:
        prompt = json.dumps(
            {
                "scan_scope": "ai-company-platform",
                "scan_type": _scan_type(task, default="platform_audit"),
                "payload": task.payload,
                "context": task.context,
            },
            default=str,
            sort_keys=True,
        )
        try:
            raw = await self.agent_loop.run_structured(
                task=prompt,
                system_prompt=_PLATFORMSEC_SYSTEM,
                enable_caching=self.agent_config.enable_caching,
                model=self.config.preferred_model or None,
            )
            content = raw.get("content", raw) if isinstance(raw, dict) else raw
            data = _parse_json(content)
            if data:
                return _result_from_dict(data, fallback_scan_type="platform_audit")
        except Exception as exc:
            return SecurityScanResult(
                scan_type="platform_audit",
                severity="info",
                findings=[],
                risk_score=0.0,
                raw_output_summary=f"PlatformSec audit could not complete: {exc}",
            )
        return SecurityScanResult(
            scan_type="platform_audit",
            severity="info",
            findings=[],
            risk_score=0.0,
            raw_output_summary="No actionable PlatformSec findings were returned.",
        )
