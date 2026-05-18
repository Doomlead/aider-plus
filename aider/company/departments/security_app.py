from __future__ import annotations

import json
import shutil
import uuid
from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.department import Department
from aider.company.schemas import CompanyTask, Deliverable, SecurityScanResult
from aider.memory import ConversationMemory, ProjectMemory

APPSEC_ALLOWLISTED_SCANS = {
    "pip-audit": ["pip-audit", "--format", "json"],
    "safety": ["safety", "check", "--json"],
    "bandit": ["bandit", "-q", "-r", ".", "-f", "json"],
    "semgrep": ["semgrep", "scan", "--config", "auto", "--json"],
}


_APPSEC_SYSTEM = """You are the AppSec Department for an AI software company.
Focus on the product being built: dependency vulnerability scanning, secure coding
review, product threat modeling, and pentest guidance. Return strict JSON with:
scan_type (vuln|pentest|code_review), severity (critical|high|medium|low|info),
findings [{location, description, recommendation, cve?}], fixed_count,
risk_score, and raw_output_summary. Prefer actionable, patch-ready findings."""


class AppSecDepartment(Department):
    """Application Security department for product-facing security work."""

    name = "security_app"
    allowed_tools = ["file_read", "repo_search", "dependency_scan", "aider_coder"]

    def __init__(
        self,
        project_memory: ProjectMemory,
        agent_loop: AiderAgentLoop,
        conversation_memory: Optional[ConversationMemory] = None,
        config: Optional[DepartmentConfig] = None,
    ):
        super().__init__(project_memory, conversation_memory, config=config)
        self.agent_loop = agent_loop
        self.tools = ["file_read", "repo_search", "dependency_scan"]
        if hasattr(self.agent_loop, "tool_registry"):
            self.agent_loop.tool_registry.set_department(self)

    def get_context_requirements(self) -> list[str]:
        return [
            "project.prd",
            "project.design_spec",
            "playbook.coding_standards",
            "skills.shared",
            "skills.security_app",
        ]

    async def process(self, task: CompanyTask) -> Deliverable:
        result = await self._run_scan(task)
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

    async def _run_scan(self, task: CompanyTask) -> SecurityScanResult:
        prompt = json.dumps(
            {
                "scan_scope": "application/product",
                "scan_type": _scan_type(task, default="vuln"),
                "payload": task.payload,
                "context": task.context,
                "common_scan_plan": _common_scan_plan(),
            },
            default=str,
            sort_keys=True,
        )
        try:
            raw = await self.agent_loop.run_structured(
                task=prompt,
                system_prompt=_APPSEC_SYSTEM,
                enable_caching=self.agent_config.enable_caching,
                model=self.config.preferred_model or None,
            )
            content = raw.get("content", raw) if isinstance(raw, dict) else raw
            data = _parse_json(content)
            if data:
                return _result_from_dict(data, fallback_scan_type="vuln")
        except Exception as exc:
            return SecurityScanResult(
                scan_type="vuln",
                severity="info",
                findings=[],
                risk_score=0.0,
                raw_output_summary=f"AppSec scan could not complete: {exc}",
            )
        return SecurityScanResult(
            scan_type="vuln",
            severity="info",
            findings=[],
            risk_score=0.0,
            raw_output_summary="No actionable AppSec findings were returned.",
        )


def _common_scan_plan() -> dict:
    available = {}
    missing = {}
    for name, command in APPSEC_ALLOWLISTED_SCANS.items():
        if shutil.which(command[0]):
            available[name] = command
        else:
            missing[name] = (
                f"{command[0]} is not installed; skip gracefully or recommend installation."
            )
    return {
        "allowlisted_commands": APPSEC_ALLOWLISTED_SCANS,
        "available_commands": available,
        "missing_tools": missing,
        "policy": "Only run allowlisted read-only scanners; never mutate source during scanning.",
    }


def _parse_json(value) -> dict:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
    return {}


def _result_from_dict(data: dict, *, fallback_scan_type: str) -> SecurityScanResult:
    scan_type = (
        data.get("scan_type")
        if data.get("scan_type") in {"vuln", "pentest", "code_review", "platform_audit"}
        else fallback_scan_type
    )
    severity = (
        data.get("severity")
        if data.get("severity") in {"critical", "high", "medium", "low", "info"}
        else "info"
    )
    findings = data.get("findings") if isinstance(data.get("findings"), list) else []
    normalized = []
    for index, finding in enumerate(findings, start=1):
        if isinstance(finding, dict):
            normalized.append(
                {
                    "id": finding.get("id")
                    or finding.get("finding_id")
                    or f"finding-{index}",
                    **finding,
                }
            )
    return SecurityScanResult(
        scan_type=scan_type,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        findings=normalized,
        fixed_count=int(data.get("fixed_count", 0) or 0),
        risk_score=float(data.get("risk_score", 0.0) or 0.0),
        raw_output_summary=str(
            data.get("raw_output_summary") or data.get("summary") or ""
        ),
    )


def _scan_type(task: CompanyTask, *, default: str) -> str:
    value = (
        (task.context or {}).get("scan_type")
        if isinstance(task.context, dict)
        else None
    )
    if value in {"vuln", "pentest", "code_review", "platform_audit"}:
        return value
    if isinstance(task.payload, dict) and task.payload.get("scan_type") in {
        "vuln",
        "pentest",
        "code_review",
        "platform_audit",
    }:
        return str(task.payload["scan_type"])
    return default


def _critical_findings(result: SecurityScanResult) -> list[dict]:
    if result.severity != "critical":
        return []
    findings = result.findings or [{"description": result.raw_output_summary}]
    output = []
    for finding in findings:
        item = dict(finding)
        item.setdefault("id", item.get("finding_id") or f"sec-{uuid.uuid4().hex[:8]}")
        output.append(item)
    return output


def _status_for_severity(severity: str) -> str:
    return (
        "red"
        if severity == "critical"
        else "yellow" if severity in {"high", "medium"} else "green"
    )
