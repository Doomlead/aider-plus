from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from typing import Optional

from aider.agent.loop import AiderAgentLoop
from aider.company.config import DepartmentConfig
from aider.company.department import Department
from aider.memory import ConversationMemory, ProjectMemory
from aider.company.schemas import (
    BuildArtifact,
    CompanyTask,
    Deliverable,
    DeliveryHandover,
    DeploymentResult,
)
from aider.run_cmd import run_cmd


class DevOpsDepartment(Department):
    name = "devops"
    allowed_tools = ["shell", "docker_build", "deploy", "git_tag"]

    SAFE_COMMAND_PREFIXES = (
        ("docker", "build"),
        ("python", "-m", "build"),
        ("python3", "-m", "build"),
        ("pip", "wheel"),
        ("pip3", "wheel"),
        ("npm", "run", "build"),
        ("npm", "ci"),
        ("npm", "install"),
        ("git", "tag"),
    )
    HIGH_RISK_COMMAND_PREFIXES = (
        ("docker", "push"),
        ("kubectl",),
        ("helm",),
        ("aws",),
        ("gcloud",),
        ("az",),
        ("flyctl",),
        ("vercel",),
        ("netlify",),
    )

    def __init__(
        self,
        project_memory: ProjectMemory,
        agent_loop: Optional[AiderAgentLoop] = None,
        conversation_memory: Optional[ConversationMemory] = None,
        config: Optional[DepartmentConfig] = None,
    ):
        super().__init__(project_memory, conversation_memory, config=config)
        self.agent_loop = agent_loop

    def get_context_requirements(self) -> list[str]:
        return [
            "playbook.deployment_gotchas",
            "skills.shared",
            "skills.devops",
            "skills.deployment",
            "project.name",
            "project.phase",
        ]

    async def process(self, task: CompanyTask) -> Deliverable:
        handover = self._delivery_handover(task)
        if handover and (not handover.ready_for_devops or handover.critical_blockers):
            await self._emit_lifecycle_event(
                task.task_id,
                "devops_failure",
                {
                    "formatted": "DevOps refused release because Delivery handoff is not green.",
                    "critical_blockers": handover.critical_blockers,
                    "handover": handover.to_dict(),
                },
            )
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type="deploy_report",
                payload={
                    "summary": "DevOps release blocked by Delivery readiness gate.",
                    "delivery_handover": handover.to_dict(),
                },
                status="failure",
                metadata={
                    "blocking": True,
                    "handoff_to": "delivery",
                    "context": dict(task.context),
                    "delivery_handover": handover.to_dict(),
                },
            )

        try:
            build_artifact = await self._perform_build(handover, task)
            deployment = await self._perform_deployment(
                build_artifact,
                self._environment(task, handover),
                task,
                handover,
            )
        except Exception as exc:
            await self._emit_lifecycle_event(
                task.task_id,
                "devops_failure",
                {"formatted": f"DevOps release failed: {exc}", "error": str(exc)},
            )
            return Deliverable(
                task_id=task.task_id,
                department=self.name,
                artifact_type="deploy_report",
                payload={"summary": "DevOps release failed.", "error": str(exc)},
                status="failure",
                metadata={
                    "blocking": True,
                    "handoff_to": "engineering",
                    "context": dict(task.context),
                },
            )

        status = "success" if deployment.status == "success" else "failure"
        summary = (
            f"Built {build_artifact.name}:{build_artifact.tag} and deployed to "
            f"{deployment.environment}."
            if status == "success"
            else f"Build completed but deployment status is {deployment.status}."
        )
        return Deliverable(
            task_id=task.task_id,
            department=self.name,
            artifact_type="deploy_report",
            payload={
                "summary": summary,
                "build_artifact": build_artifact.to_dict(),
                "deployment_result": deployment.to_dict(),
                "release_artifact": build_artifact.location,
                "deploy_url": deployment.deployed_url,
                "git_tag": build_artifact.tag,
                "environment": deployment.environment,
                "playbook_guidance": task.context.get("playbook_guidance", []),
            },
            status=status,
            metadata={
                "deploy_url": deployment.deployed_url,
                "git_tag": build_artifact.tag,
                "build_artifact": build_artifact.to_dict(),
                "deployment_result": deployment.to_dict(),
                "handoff_to": "ceo" if status == "success" else "engineering",
                "blocking": status != "success",
                "context": dict(task.context),
                "delivery_handover": handover.to_dict() if handover else None,
            },
        )

    async def _perform_build(
        self, handover: DeliveryHandover | None, task: CompanyTask | None = None
    ) -> BuildArtifact:
        task = task or CompanyTask(
            "devops-build", "delivery", "devops", "deploy_request", {}
        )
        name = self._artifact_name(task, handover)
        version = self._version(task)
        tag = self._git_tag(task, version)
        commands = self._build_commands(task, name, tag)
        await self._emit_lifecycle_event(
            task.task_id,
            "devops_build_started",
            {
                "formatted": f"DevOps build started for {name}:{tag}.",
                "commands": commands,
            },
        )
        logs: list[str] = []
        for command in commands:
            logs.append(await self._run_shell(command, high_risk_allowed=False))
        if any("exit_code=0" not in log for log in logs):
            raise RuntimeError(self._summarize_logs(logs) or "Build command failed.")
        artifact_type, location = self._artifact_location(task, commands, name, tag)
        artifact = BuildArtifact(
            artifact_type=artifact_type,
            name=name,
            version=version,
            tag=tag,
            location=location,
            build_logs_summary=self._summarize_logs(logs),
        )
        await self._emit_lifecycle_event(
            task.task_id,
            "devops_build_success",
            {
                "formatted": f"DevOps build succeeded for {name}:{tag}.",
                "artifact": artifact.to_dict(),
            },
        )
        return artifact

    async def _perform_deployment(
        self,
        build_artifact: BuildArtifact,
        environment: str,
        task: CompanyTask | None = None,
        handover: DeliveryHandover | None = None,
    ) -> DeploymentResult:
        task = task or CompanyTask(
            "devops-deploy", "delivery", "devops", "deploy_request", {}
        )
        await self._emit_lifecycle_event(
            task.task_id,
            "devops_deploy_started",
            {
                "formatted": f"DevOps deployment started for {environment}.",
                "artifact": build_artifact.to_dict(),
            },
        )
        commands = self._deployment_commands(task)
        high_risk_allowed = self._high_risk_approved(task)
        logs: list[str] = []
        for command in commands:
            logs.append(
                await self._run_shell(command, high_risk_allowed=high_risk_allowed)
            )
        if commands and any("exit_code=0" not in log for log in logs):
            result = DeploymentResult(
                environment=environment,
                status="failed",
                deployed_url=None,
                rollback_url=self._rollback_url(task, handover),
                build_artifact=build_artifact,
                deployment_logs=self._summarize_logs(logs),
            )
            await self._emit_lifecycle_event(
                task.task_id,
                "devops_failure",
                {
                    "formatted": "DevOps deployment command failed.",
                    "deployment": result.to_dict(),
                },
            )
            return result

        deployed_url = self._deploy_url(task, build_artifact, environment)
        if not commands:
            logs.append(
                self._write_local_deployment_record(
                    task, build_artifact, environment, deployed_url
                )
            )
        result = DeploymentResult(
            environment=environment,
            status="success",
            deployed_url=deployed_url,
            rollback_url=self._rollback_url(task, handover),
            build_artifact=build_artifact,
            deployment_logs=self._summarize_logs(logs),
        )
        await self._emit_lifecycle_event(
            task.task_id,
            "devops_deployed",
            {
                "formatted": f"DevOps deployed {build_artifact.tag} to {environment}.",
                "deployment": result.to_dict(),
            },
        )
        return result

    async def _run_shell(self, command: str, *, high_risk_allowed: bool = False) -> str:
        if not self.can_use_tool("shell"):
            return "Permission violation: DevOps is not allowed to use shell."
        self._validate_command(command, high_risk_allowed=high_risk_allowed)
        cwd = None
        root = getattr(self.memory, "repo_path", None)
        if root:
            cwd = str(Path(root))
        returncode, output = await asyncio.to_thread(run_cmd, command, False, None, cwd)
        return f"$ {command}\nexit_code={returncode}\n{output}"

    def _validate_command(self, command: str, *, high_risk_allowed: bool) -> None:
        try:
            parts = tuple(shlex.split(command))
        except ValueError as exc:
            raise PermissionError(f"Invalid DevOps command: {command}") from exc
        if not parts:
            raise PermissionError("Empty DevOps command is not allowed.")
        if any(
            self._matches_prefix(parts, prefix) for prefix in self.SAFE_COMMAND_PREFIXES
        ):
            return
        if any(
            self._matches_prefix(parts, prefix)
            for prefix in self.HIGH_RISK_COMMAND_PREFIXES
        ):
            if high_risk_allowed:
                return
            raise PermissionError(
                f"High-risk DevOps command requires approval: {command}"
            )
        raise PermissionError(f"DevOps command is not in the allowlist: {command}")

    @staticmethod
    def _matches_prefix(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
        return len(parts) >= len(prefix) and parts[: len(prefix)] == prefix

    def _delivery_handover(self, task: CompanyTask) -> DeliveryHandover | None:
        payload = task.payload if isinstance(task.payload, dict) else {}
        handover = payload.get("delivery_handover") or task.context.get(
            "delivery_handover"
        )
        delivery_metadata = payload.get("delivery_metadata") or {}
        if not handover and isinstance(delivery_metadata, dict):
            handover = delivery_metadata.get("delivery_handover")
        if isinstance(handover, DeliveryHandover):
            return handover
        if isinstance(handover, dict):
            return DeliveryHandover.from_dict(handover)
        return None

    def _build_commands(self, task: CompanyTask, name: str, tag: str) -> list[str]:
        payload = task.payload if isinstance(task.payload, dict) else {}
        configured = payload.get("build_commands") or task.context.get("build_commands")
        if configured:
            return [str(cmd) for cmd in configured]
        root = Path(getattr(self.memory, "repo_path", "."))
        if (root / "Dockerfile").exists():
            return [f"docker build -t {shlex.quote(name)}:{shlex.quote(tag)} ."]
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            return ["python -m build"]
        if (root / "package.json").exists():
            return ["npm run build"]
        return []

    def _deployment_commands(self, task: CompanyTask) -> list[str]:
        payload = task.payload if isinstance(task.payload, dict) else {}
        commands = (
            payload.get("deployment_commands")
            or task.context.get("deployment_commands")
            or []
        )
        return [str(cmd) for cmd in commands]

    def _artifact_location(
        self, task: CompanyTask, commands: list[str], name: str, tag: str
    ) -> tuple[str, str]:
        payload = task.payload if isinstance(task.payload, dict) else {}
        if payload.get("artifact_location"):
            return str(payload.get("artifact_type") or "unknown"), str(
                payload["artifact_location"]
            )
        first = commands[0] if commands else ""
        if first.startswith("docker build"):
            return "docker_image", f"{name}:{tag}"
        if "python -m build" in first or "pip wheel" in first:
            root = Path(getattr(self.memory, "repo_path", "."))
            return "pypi_package", str(root / "dist")
        if first.startswith("npm run build"):
            root = Path(getattr(self.memory, "repo_path", "."))
            return "static_site", str(root / "dist")
        root = Path(getattr(self.memory, "repo_path", "."))
        return "source_snapshot", str(root)

    def _write_local_deployment_record(
        self,
        task: CompanyTask,
        artifact: BuildArtifact,
        environment: str,
        deployed_url: str,
    ) -> str:
        root = Path(getattr(self.memory, "repo_path", "."))
        out_dir = root / ".aider" / "company" / "deployments" / environment
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{artifact.tag}.json"
        path.write_text(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "environment": environment,
                    "deployed_url": deployed_url,
                    "build_artifact": artifact.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return f"Recorded local deployment manifest: {path}"

    def _artifact_name(
        self, task: CompanyTask, handover: DeliveryHandover | None
    ) -> str:
        payload = task.payload if isinstance(task.payload, dict) else {}
        raw = (
            payload.get("artifact_name")
            or (handover.project_name if handover else None)
            or task.context.get("project_name")
            or "app"
        )
        return (
            "-".join(
                part
                for part in str(raw).strip().lower().replace("_", "-").split()
                if part
            )
            or "app"
        )

    @staticmethod
    def _version(task: CompanyTask) -> str:
        if isinstance(task.payload, dict) and task.payload.get("version"):
            return str(task.payload["version"])
        return str(task.context.get("version") or "1.0.0")

    @staticmethod
    def _environment(
        task: CompanyTask, handover: DeliveryHandover | None = None
    ) -> str:
        if isinstance(task.payload, dict) and task.payload.get("environment"):
            return str(task.payload.get("environment"))
        if handover:
            return handover.environment
        return str(task.context.get("environment") or "production")

    @classmethod
    def _deploy_url(
        cls,
        task: CompanyTask,
        artifact: BuildArtifact | None = None,
        environment: str | None = None,
    ) -> str:
        if isinstance(task.payload, dict) and task.payload.get("deploy_url"):
            return str(task.payload["deploy_url"])
        project_name = (
            str(
                task.context.get("project_name")
                or (artifact.name if artifact else "app")
            )
            .strip()
            .lower()
        )
        safe_name = "-".join(
            part for part in project_name.replace("_", "-").split() if part
        )
        env_prefix = (
            "" if (environment or "production") == "production" else f"{environment}-"
        )
        return f"https://{env_prefix}{safe_name or 'app'}.example.com"

    @staticmethod
    def _rollback_url(
        task: CompanyTask, handover: DeliveryHandover | None = None
    ) -> str | None:
        if isinstance(task.payload, dict) and task.payload.get("rollback_url"):
            return str(task.payload["rollback_url"])
        if handover and handover.rollback_plan:
            return "rollback://delivery-plan"
        return None

    @staticmethod
    def _git_tag(task: CompanyTask, version: str = "1.0.0") -> str:
        if isinstance(task.payload, dict) and task.payload.get("git_tag"):
            return str(task.payload["git_tag"])
        tag = str(task.context.get("git_tag") or version)
        return tag if tag.startswith("v") else f"v{tag}"

    @staticmethod
    def _high_risk_approved(task: CompanyTask) -> bool:
        payload = task.payload if isinstance(task.payload, dict) else {}
        return bool(
            payload.get("devops_high_risk_approved")
            or payload.get("deploy_approved")
            or task.context.get("devops_high_risk_approved")
        )

    @staticmethod
    def _summarize_logs(logs: list[str], limit: int = 1200) -> str:
        return "\n---\n".join(logs)[-limit:]

    @staticmethod
    def _release_artifact(task: CompanyTask):
        if isinstance(task.payload, dict):
            return task.payload.get("engineering_result") or task.payload.get(
                "release_artifact"
            )
        return task.payload
