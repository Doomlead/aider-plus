from __future__ import annotations

import asyncio
import json
import re
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
    DeploymentTarget,
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
        ("make", "build"),
        ("cargo", "build"),
        ("go", "build"),
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
        ("railway",),
        ("docker", "compose"),
        ("docker-compose",),
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
            target = self._deployment_target(task, handover)
            deployment = await self._perform_deployment(
                build_artifact,
                target.environment,
                task,
                handover,
                target,
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
                "logs_url": deployment.logs_url,
                "rollback_command": deployment.rollback_command,
                "git_tag": build_artifact.tag,
                "environment": deployment.environment,
                "deployment_target": (
                    deployment.target.to_dict() if deployment.target else None
                ),
                "build_logs_summary": build_artifact.build_logs_summary,
                "deployment_logs_summary": deployment.deployment_logs,
                "log_artifacts": (
                    list(build_artifact.log_artifacts) + list(deployment.log_artifacts)
                ),
                "artifact_links": [build_artifact.location],
                "playbook_guidance": task.context.get("playbook_guidance", []),
                "skill_guidance": task.context.get("skill_guidance", []),
            },
            status=status,
            metadata={
                "deploy_url": deployment.deployed_url,
                "logs_url": deployment.logs_url,
                "rollback_command": deployment.rollback_command,
                "git_tag": build_artifact.tag,
                "build_artifact": build_artifact.to_dict(),
                "deployment_result": deployment.to_dict(),
                "log_artifacts": (
                    list(build_artifact.log_artifacts) + list(deployment.log_artifacts)
                ),
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
                "skill_guidance": task.context.get("skill_guidance", []),
            },
        )
        logs: list[str] = []
        for command in commands:
            logs.append(
                await self._run_shell_with_retry(
                    command, task=task, phase="build", high_risk_allowed=False
                )
            )
        log_artifacts = self._capture_logs(task, "build", logs)
        if any(not self._command_succeeded(log) for log in logs):
            raise RuntimeError(self._summarize_logs(logs) or "Build command failed.")
        artifact_type, location = self._artifact_location(task, commands, name, tag)
        artifact = BuildArtifact(
            artifact_type=artifact_type,
            name=name,
            version=version,
            tag=tag,
            location=location,
            build_logs_summary=self._summarize_logs(logs),
            log_artifacts=log_artifacts,
            detected_command_source=self._build_command_source(task, commands),
        )
        await self._emit_lifecycle_event(
            task.task_id,
            "devops_build_success",
            {
                "formatted": f"DevOps build succeeded for {name}:{tag}.",
                "artifact": artifact.to_dict(),
                "log_artifacts": log_artifacts,
                "logs_summary": artifact.build_logs_summary,
            },
        )
        return artifact

    async def _perform_deployment(
        self,
        build_artifact: BuildArtifact,
        environment: str,
        task: CompanyTask | None = None,
        handover: DeliveryHandover | None = None,
        target: DeploymentTarget | None = None,
    ) -> DeploymentResult:
        task = task or CompanyTask(
            "devops-deploy", "delivery", "devops", "deploy_request", {}
        )
        target = target or self._deployment_target(task, handover, environment)
        self._validate_target_provider(target)
        await self._emit_lifecycle_event(
            task.task_id,
            "devops_deploy_started",
            {
                "formatted": (
                    f"DevOps deployment started for {target.provider} / {target.environment}."
                ),
                "target": target.to_dict(),
                "artifact": build_artifact.to_dict(),
            },
        )
        commands = self._deployment_commands(task, target, build_artifact)
        high_risk_allowed = self._high_risk_approved(task)
        logs: list[str] = []
        for command in commands:
            logs.append(
                await self._run_shell_with_retry(
                    command,
                    task=task,
                    phase="deploy",
                    high_risk_allowed=high_risk_allowed,
                )
            )
        log_artifacts = self._capture_logs(task, "deploy", logs)
        if commands and any(not self._command_succeeded(log) for log in logs):
            result = DeploymentResult(
                environment=target.environment,
                status="failed",
                target=target,
                deployed_url=None,
                logs_url=self._logs_url(task, target, log_artifacts),
                rollback_url=self._rollback_url(task, handover),
                rollback_command=self._rollback_command(
                    task, target, build_artifact, handover
                ),
                build_artifact=build_artifact,
                deployment_logs=self._summarize_logs(logs),
                log_artifacts=log_artifacts,
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

        deployed_url = self._deploy_url(
            task, build_artifact, target.environment, target
        )
        if not commands:
            logs.append(
                self._write_local_deployment_record(
                    task, build_artifact, target.environment, deployed_url, target
                )
            )
            log_artifacts = self._capture_logs(task, "deploy", logs)
        result = DeploymentResult(
            environment=target.environment,
            status="success",
            target=target,
            deployed_url=deployed_url,
            logs_url=self._logs_url(task, target, log_artifacts),
            rollback_url=self._rollback_url(task, handover),
            rollback_command=self._rollback_command(
                task, target, build_artifact, handover
            ),
            build_artifact=build_artifact,
            deployment_logs=self._summarize_logs(logs),
            log_artifacts=log_artifacts,
        )
        await self._emit_lifecycle_event(
            task.task_id,
            "devops_deployed",
            {
                "formatted": (
                    f"DevOps deployed {build_artifact.tag} to "
                    f"{target.provider} / {target.environment}."
                ),
                "target": target.to_dict(),
                "deployment": result.to_dict(),
            },
        )
        return result

    async def _run_shell_with_retry(
        self,
        command: str,
        *,
        task: CompanyTask,
        phase: str,
        high_risk_allowed: bool = False,
    ) -> str:
        attempts = self._retry_attempts(task)
        delay = self._retry_base_delay(task)
        attempt_logs: list[str] = []
        for attempt in range(1, attempts + 1):
            log = await self._run_shell(command, high_risk_allowed=high_risk_allowed)
            attempt_logs.append(f"attempt={attempt}/{attempts}\n{log}")
            if self._command_succeeded(log):
                return "\n--- retry-attempt ---\n".join(attempt_logs)
            if attempt >= attempts or not self._is_transient_failure(log):
                return "\n--- retry-attempt ---\n".join(attempt_logs)
            await self._emit_lifecycle_event(
                task.task_id,
                "devops_command_retry",
                {
                    "formatted": (
                        f"DevOps retrying transient {phase} command after attempt {attempt}."
                    ),
                    "command": command,
                    "phase": phase,
                    "attempt": attempt,
                    "max_attempts": attempts,
                    "backoff_seconds": delay * (2 ** (attempt - 1)),
                },
            )
            await asyncio.sleep(delay * (2 ** (attempt - 1)))
        return "\n--- retry-attempt ---\n".join(attempt_logs)

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
        package_json = root / "package.json"
        if package_json.exists():
            scripts = self._package_scripts(package_json)
            if "build" in scripts:
                commands = []
                if (root / "package-lock.json").exists():
                    commands.append("npm ci")
                commands.append("npm run build")
                return commands
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            return ["python -m build"]
        if (root / "Makefile").exists() or (root / "makefile").exists():
            return ["make build"]
        if (root / "Cargo.toml").exists():
            return ["cargo build --release"]
        if (root / "go.mod").exists():
            return ["go build ./..."]
        return self._fallback_build_commands(task)

    def _fallback_build_commands(self, task: CompanyTask) -> list[str]:
        payload = task.payload if isinstance(task.payload, dict) else {}
        configured = (
            payload.get("fallback_build_commands")
            or task.context.get("fallback_build_commands")
            or getattr(self.config, "devops_build_fallback_commands", [])
        )
        return [str(cmd) for cmd in configured or []]

    def _build_command_source(self, task: CompanyTask, commands: list[str]) -> str:
        payload = task.payload if isinstance(task.payload, dict) else {}
        if payload.get("build_commands") or task.context.get("build_commands"):
            return "task_configured"
        if commands == self._fallback_build_commands(task):
            return "fallback_configured" if commands else "none"
        root = Path(getattr(self.memory, "repo_path", "."))
        for marker in (
            "Dockerfile",
            "package.json",
            "pyproject.toml",
            "setup.py",
            "Makefile",
            "makefile",
            "Cargo.toml",
            "go.mod",
        ):
            if (root / marker).exists():
                return f"auto_detected:{marker}"
        return "auto_detected" if commands else "none"

    @staticmethod
    def _package_scripts(path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        scripts = data.get("scripts", {})
        return scripts if isinstance(scripts, dict) else {}

    def _deployment_commands(
        self,
        task: CompanyTask,
        target: DeploymentTarget | None = None,
        artifact: BuildArtifact | None = None,
    ) -> list[str]:
        payload = task.payload if isinstance(task.payload, dict) else {}
        commands = (
            payload.get("deployment_commands")
            or task.context.get("deployment_commands")
            or []
        )
        if commands:
            return [str(cmd) for cmd in commands]
        if target is None or target.provider == "local":
            return []
        return self._commands_for_target(task, target, artifact)

    def _commands_for_target(
        self,
        task: CompanyTask,
        target: DeploymentTarget,
        artifact: BuildArtifact | None,
    ) -> list[str]:
        provider = self._normalize_provider(target.provider)
        cfg = dict(target.config or {})
        if provider == "vercel":
            command = "vercel deploy --yes"
            command += (
                " --prod" if target.environment == "production" else " --target preview"
            )
            if cfg.get("scope"):
                command += f" --scope {shlex.quote(str(cfg['scope']))}"
            return [command]
        if provider == "railway":
            command = "railway up --detach"
            if cfg.get("service"):
                command += f" --service {shlex.quote(str(cfg['service']))}"
            if target.environment:
                command += f" --environment {shlex.quote(target.environment)}"
            return [command]
        if provider == "fly":
            command = "flyctl deploy --remote-only"
            if cfg.get("app"):
                command += f" --app {shlex.quote(str(cfg['app']))}"
            if cfg.get("config"):
                command += f" --config {shlex.quote(str(cfg['config']))}"
            return [command]
        if provider == "aws":
            if cfg.get("s3_bucket"):
                source = shlex.quote(
                    str(
                        cfg.get("source") or (artifact.location if artifact else "dist")
                    )
                )
                bucket = shlex.quote(str(cfg["s3_bucket"]))
                return [f"aws s3 sync {source} s3://{bucket} --delete"]
            app = shlex.quote(
                str(cfg.get("application") or (artifact.name if artifact else "app"))
            )
            group = shlex.quote(str(cfg.get("deployment_group") or target.environment))
            return [
                f"aws deploy create-deployment --application-name {app} --deployment-group-name {group}"
            ]
        if provider == "docker-compose":
            service = (
                f" {shlex.quote(str(cfg['service']))}" if cfg.get("service") else ""
            )
            compose_file = (
                f" -f {shlex.quote(str(cfg['file']))}" if cfg.get("file") else ""
            )
            return [f"docker compose{compose_file} up -d --build{service}"]
        raise PermissionError(f"Unsupported deployment provider: {target.provider}")

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
        target: DeploymentTarget | None = None,
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
                    "provider": target.provider if target else "local",
                    "deployed_url": deployed_url,
                    "deployment_target": target.to_dict() if target else None,
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

    def _deployment_target(
        self,
        task: CompanyTask,
        handover: DeliveryHandover | None = None,
        environment: str | None = None,
    ) -> DeploymentTarget:
        payload = task.payload if isinstance(task.payload, dict) else {}
        raw = (
            payload.get("deployment_target")
            or task.context.get("deployment_target")
            or (handover.deployment_target if handover else None)
        )
        if isinstance(raw, DeploymentTarget):
            target = raw
        elif isinstance(raw, dict):
            target = DeploymentTarget.from_dict(raw)
        else:
            target = DeploymentTarget(
                provider=str(
                    payload.get("deployment_provider")
                    or task.context.get("deployment_provider")
                    or getattr(
                        self.config, "devops_default_deployment_provider", "local"
                    )
                    or "local"
                ),
                environment=environment or self._environment(task, handover),
                config=dict(
                    payload.get("deployment_config")
                    or task.context.get("deployment_config")
                    or {}
                ),
            )
        raw_missing_environment = isinstance(raw, dict) and not raw.get("environment")
        if environment and (not target.environment or raw_missing_environment):
            target.environment = environment
        if raw_missing_environment:
            target.environment = self._environment(task, handover)
        target.provider = self._normalize_provider(target.provider)
        target.environment = target.environment or self._environment(task, handover)
        return target

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = str(provider or "local").strip().lower().replace("_", "-")
        aliases = {
            "fly.io": "fly",
            "flyio": "fly",
            "compose": "docker-compose",
            "docker": "docker-compose",
        }
        return aliases.get(normalized, normalized or "local")

    def _validate_target_provider(self, target: DeploymentTarget) -> None:
        allowed = {
            self._normalize_provider(provider)
            for provider in getattr(self.config, "devops_deployment_providers", [])
        }
        if not allowed:
            allowed = {"local"}
        if target.provider not in allowed:
            raise PermissionError(
                f"Deployment provider {target.provider!r} is not enabled; allowed providers: "
                f"{', '.join(sorted(allowed))}"
            )

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
        target: DeploymentTarget | None = None,
    ) -> str:
        if isinstance(task.payload, dict) and task.payload.get("deploy_url"):
            return str(task.payload["deploy_url"])
        if target and target.config.get("deployed_url"):
            return str(target.config["deployed_url"])
        if target and target.provider == "vercel" and target.config.get("project"):
            project = str(target.config["project"]).strip().lower()
            return f"https://{project}.vercel.app"
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

    def _logs_url(
        self, task: CompanyTask, target: DeploymentTarget, log_artifacts: list[str]
    ) -> str | None:
        payload = task.payload if isinstance(task.payload, dict) else {}
        if payload.get("logs_url"):
            return str(payload["logs_url"])
        if target.config.get("logs_url"):
            return str(target.config["logs_url"])
        uploaded = self._upload_log_artifacts(log_artifacts, target)
        return uploaded[0] if uploaded else None

    def _upload_log_artifacts(
        self, log_artifacts: list[str], target: DeploymentTarget
    ) -> list[str]:
        upload_target = str(
            getattr(self.config, "devops_artifact_upload_target", "") or ""
        )
        if not upload_target or not log_artifacts:
            return []
        safe_provider = re.sub(r"[^A-Za-z0-9_.-]+", "-", target.provider)
        if upload_target.startswith("s3://"):
            prefix = upload_target.rstrip("/")
            return [
                f"{prefix}/{safe_provider}/{Path(path).name}" for path in log_artifacts
            ]
        if upload_target.startswith("github://"):
            prefix = upload_target.rstrip("/")
            return [
                f"{prefix}/{safe_provider}/{Path(path).name}" for path in log_artifacts
            ]
        return []

    def _rollback_command(
        self,
        task: CompanyTask,
        target: DeploymentTarget,
        artifact: BuildArtifact,
        handover: DeliveryHandover | None = None,
    ) -> str | None:
        payload = task.payload if isinstance(task.payload, dict) else {}
        if payload.get("rollback_command"):
            return str(payload["rollback_command"])
        if target.config.get("rollback_command"):
            return str(target.config["rollback_command"])
        previous = payload.get("previous_artifact") or task.context.get(
            "previous_artifact"
        )
        if target.provider == "vercel":
            return "vercel rollback"
        if target.provider == "railway":
            return "railway rollback"
        if target.provider == "fly":
            return "flyctl releases rollback"
        if target.provider == "docker-compose":
            return "docker compose down"
        if target.provider == "aws" and previous:
            return (
                f"aws deploy create-deployment --revision {shlex.quote(str(previous))}"
            )
        if handover and handover.rollback_plan:
            return "Follow Delivery rollback plan"
        return None

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

    def _capture_logs(
        self, task: CompanyTask, phase: str, logs: list[str]
    ) -> list[str]:
        if not logs:
            return []
        root = Path(getattr(self.memory, "repo_path", "."))
        log_dir = Path(
            getattr(self.config, "devops_log_capture_dir", ".aider/company/build-logs")
        )
        if not log_dir.is_absolute():
            log_dir = root / log_dir
        safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "-", task.task_id or "devops")[:120]
        task_dir = log_dir / safe_task
        task_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for idx, log in enumerate(logs, start=1):
            path = task_dir / f"{phase}-{idx}.log"
            path.write_text(log, encoding="utf-8")
            paths.append(str(path))
        return paths

    @staticmethod
    def _command_succeeded(log: str) -> bool:
        return bool(re.search(r"(^|\n)exit_code=0(\n|$)", log))

    @staticmethod
    def _is_transient_failure(log: str) -> bool:
        lowered = log.lower()
        transient_markers = (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "connection reset",
            "connection refused",
            "econnreset",
            "etimedout",
            "network",
            "rate limit",
            "too many requests",
            "503",
            "502",
            "504",
        )
        return any(marker in lowered for marker in transient_markers)

    def _retry_attempts(self, task: CompanyTask) -> int:
        payload = task.payload if isinstance(task.payload, dict) else {}
        raw = payload.get("devops_retry_attempts") or task.context.get(
            "devops_retry_attempts"
        )
        if raw is None:
            raw = getattr(self.config, "devops_retry_attempts", 3)
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 3

    def _retry_base_delay(self, task: CompanyTask) -> float:
        payload = task.payload if isinstance(task.payload, dict) else {}
        raw = payload.get("devops_retry_base_delay") or task.context.get(
            "devops_retry_base_delay"
        )
        if raw is None:
            raw = getattr(self.config, "devops_retry_base_delay", 0.25)
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 0.25

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
