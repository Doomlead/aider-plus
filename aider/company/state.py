from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

from aider.company.audit import append_audit_event
from aider.company.project import Project
from aider.memory import ProjectMemory
from aider.memory.records import MemoryRecord
from aider.memory.store import MemoryStore


class CompanyStateManager:
    """Single owner for company workflow state persisted in ProjectMemory."""

    def __init__(self, project_memory: ProjectMemory):
        self._memory = project_memory
        self.active_project: Optional[Project] = None
        self._memory_store: Optional[MemoryStore] = None

    @property
    def memory(self) -> ProjectMemory:
        return self._memory

    def get_project_id(self) -> str:
        if self.active_project:
            return self.active_project.project_id
        return str(
            self._memory.data.get("project_id")
            or getattr(self._memory, "repo_path", "")
        )

    def get_current_phase(self) -> Optional[str]:
        return self.active_project.phase if self.active_project else None

    def set_current_phase(self, phase: str) -> None:
        if self.active_project:
            self.active_project.phase = phase
        self._memory.update({"current_project_phase": phase})
        self._memory.persist()

    def add_pending_approval(self, approval: dict) -> None:
        approvals = [
            item
            for item in self.get_pending_approvals()
            if item.get("task_id") != approval.get("task_id")
        ]
        approvals.append(approval)
        self._memory.update({"pending_approvals": approvals})
        self._memory.persist()

    def remove_pending_approval(self, task_id: str) -> None:
        approvals = [
            item
            for item in self.get_pending_approvals()
            if item.get("task_id") != task_id
        ]
        self._memory.update({"pending_approvals": approvals})
        self._memory.persist()

    def get_pending_approvals(self) -> List[dict]:
        approvals = self._memory.data.get("pending_approvals", [])
        if not isinstance(approvals, list):
            return []
        return [item for item in approvals if isinstance(item, dict)]


    def _store(self) -> MemoryStore:
        if self._memory_store is None:
            self._memory_store = MemoryStore(self._memory)
        return self._memory_store

    def get_playbook(self) -> dict:
        canonical: dict[str, list[dict[str, Any]]] = {}
        records = self._store().query_records(kind="playbook")
        for record in records:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            category = str(metadata.get("playbook_category") or "coding_standards")
            canonical.setdefault(category, []).append(record.content)
        if canonical:
            return canonical
        playbook = self._memory.data.get("playbook", {})
        return playbook if isinstance(playbook, dict) else {}

    def save_playbook(self, playbook: dict) -> None:
        if not isinstance(playbook, dict):
            return
        for category, entries in playbook.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                self._store().append_record(
                    MemoryRecord(
                        kind="playbook",
                        content=entry,
                        scope=f"project:{self.get_project_id()}",
                        visibility="project",
                        department="orchestrator",
                        project_id=self.get_project_id(),
                        metadata={"playbook_category": str(category)},
                    )
                )

    def get_observability(self) -> dict:
        observability = self._memory.data.get("observability", {})
        if not isinstance(observability, dict):
            observability = {}
        observability.setdefault("turns_per_phase", {})
        observability.setdefault("token_usage_per_department", {})
        observability.setdefault(
            "qa_metrics",
            {
                "total_runs": 0,
                "passed": 0,
                "failed": 0,
                "no_tests": 0,
                "pass_rate": 0.0,
            },
        )
        observability.setdefault(
            "task_metrics",
            {
                "total_tasks": 0,
                "qa_revision_cycles": 0,
                "engineering_revision_cycles": 0,
                "avg_qa_revisions": 0.0,
            },
        )
        return observability

    def record_phase_turn(self, phase: Optional[str], department: str) -> None:
        phase_name = phase or "unassigned"
        observability = self.get_observability()
        turns = observability.setdefault("turns_per_phase", {})
        phase_turns = turns.setdefault(phase_name, {})
        phase_turns[department] = int(phase_turns.get(department, 0) or 0) + 1
        self._memory.update({"observability": observability})
        self._memory.persist()

    # Approximate cost per 1M tokens by model family (input rate used as estimate).
    # Update as needed; this is intentionally conservative and approximate.
    _COST_PER_1M_TOKENS: dict[str, float] = {
        "claude-opus": 15.00,
        "claude-sonnet": 3.00,
        "claude-haiku": 0.25,
        "gpt-4o": 5.00,
        "gpt-4": 30.00,
        "gpt-3.5": 0.50,
        "deepseek": 0.14,
        "default": 1.00,
    }

    def record_department_tokens(
        self,
        department: str,
        token_usage: Any,
        *,
        model: Optional[str] = None,
        cache_enabled: Optional[bool] = None,
    ) -> None:
        """
        Record a token usage event for *department*.

        token_usage may be:
          - None / 0           → ignored
          - int                → treated as total_tokens only
          - dict               → expects keys: total_tokens, prompt_tokens,
                                 completion_tokens (all optional, all int)
        """
        parsed = self._parse_token_usage(token_usage)
        if parsed["total_tokens"] <= 0:
            return

        cost = self._estimate_cost(parsed, model)
        observability = self.get_observability()
        usage_map = observability.setdefault("token_usage_per_department", {})

        dept_record = usage_map.get(department)
        if not isinstance(dept_record, dict):
            # Handle legacy int records or missing keys.
            legacy = int(dept_record) if isinstance(dept_record, (int, float)) else 0
            dept_record = {
                "total_tokens": legacy,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "estimated_cost_usd": 0.0,
                "run_count": 0,
                "cached_runs": 0,
                "uncached_runs": 0,
            }
        else:
            dept_record = {
                "total_tokens": int(dept_record.get("total_tokens", 0) or 0),
                "prompt_tokens": int(dept_record.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(dept_record.get("completion_tokens", 0) or 0),
                "estimated_cost_usd": float(
                    dept_record.get("estimated_cost_usd", 0.0) or 0.0
                ),
                "run_count": int(dept_record.get("run_count", 0) or 0),
                "cached_runs": int(dept_record.get("cached_runs", 0) or 0),
                "uncached_runs": int(dept_record.get("uncached_runs", 0) or 0),
            }

        dept_record["total_tokens"] += parsed["total_tokens"]
        dept_record["prompt_tokens"] += parsed["prompt_tokens"]
        dept_record["completion_tokens"] += parsed["completion_tokens"]
        dept_record["estimated_cost_usd"] = round(
            dept_record["estimated_cost_usd"] + cost, 6
        )
        dept_record["run_count"] += 1
        if cache_enabled is True:
            dept_record["cached_runs"] += 1
        elif cache_enabled is False:
            dept_record["uncached_runs"] += 1
        usage_map[department] = dept_record

        self._memory.update({"observability": observability})
        self._memory.persist()

    def record_qa_result(self, test_passed: Optional[bool]) -> None:
        """
        Record a QA run outcome for pass-rate tracking.

        Call this from the orchestrator immediately after QA produces a deliverable.

        Args:
            test_passed: True = passed, False = failed, None = no tests found.
        """
        observability = self.get_observability()
        qa = observability.setdefault(
            "qa_metrics",
            {
                "total_runs": 0,
                "passed": 0,
                "failed": 0,
                "no_tests": 0,
                "pass_rate": 0.0,
            },
        )
        qa["total_runs"] = int(qa.get("total_runs", 0) or 0) + 1
        if test_passed is True:
            qa["passed"] = int(qa.get("passed", 0) or 0) + 1
        elif test_passed is False:
            qa["failed"] = int(qa.get("failed", 0) or 0) + 1
        else:
            qa["no_tests"] = int(qa.get("no_tests", 0) or 0) + 1

        total = qa["total_runs"]
        qa["pass_rate"] = round(qa["passed"] / total, 4) if total > 0 else 0.0
        observability["qa_metrics"] = qa
        self._memory.update({"observability": observability})
        self._memory.persist()

    def record_task_iteration(
        self,
        *,
        qa_revision: bool = False,
        engineering_revision: bool = False,
    ) -> None:
        """
        Record a revision cycle event for convergence tracking.

        Call with qa_revision=True each time QA routes back to Engineering,
        and engineering_revision=True each time Engineering internally retries.
        """
        observability = self.get_observability()
        tm = observability.setdefault(
            "task_metrics",
            {
                "total_tasks": 0,
                "qa_revision_cycles": 0,
                "engineering_revision_cycles": 0,
                "avg_qa_revisions": 0.0,
            },
        )

        if qa_revision:
            tm["qa_revision_cycles"] = int(tm.get("qa_revision_cycles", 0) or 0) + 1
        if engineering_revision:
            tm["engineering_revision_cycles"] = (
                int(tm.get("engineering_revision_cycles", 0) or 0) + 1
            )

        # Recompute avg QA revisions per task (approximate: cycles / tasks).
        total = int(tm.get("total_tasks", 0) or 0)
        if total > 0:
            tm["avg_qa_revisions"] = round(
                int(tm.get("qa_revision_cycles", 0) or 0) / total, 2
            )
        observability["task_metrics"] = tm
        self._memory.update({"observability": observability})
        self._memory.persist()

    def increment_task_count(self) -> None:
        """Call once when a new top-level company task begins."""
        observability = self.get_observability()
        tm = observability.setdefault(
            "task_metrics",
            {
                "total_tasks": 0,
                "qa_revision_cycles": 0,
                "engineering_revision_cycles": 0,
                "avg_qa_revisions": 0.0,
            },
        )
        tm["total_tasks"] = int(tm.get("total_tasks", 0) or 0) + 1
        total = int(tm.get("total_tasks", 0) or 0)
        if total > 0:
            tm["avg_qa_revisions"] = round(
                int(tm.get("qa_revision_cycles", 0) or 0) / total, 2
            )
        observability["task_metrics"] = tm
        self._memory.update({"observability": observability})
        self._memory.persist()

    @staticmethod
    def _parse_token_usage(token_usage: Any) -> dict[str, int]:
        """Return a dict with total_tokens, prompt_tokens, completion_tokens."""
        if isinstance(token_usage, int):
            return {
                "total_tokens": token_usage,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
        if not isinstance(token_usage, dict):
            return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}

        prompt = int(
            token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0
        )
        completion = int(
            token_usage.get("completion_tokens")
            or token_usage.get("output_tokens")
            or 0
        )
        total = int(
            token_usage.get("total_tokens")
            or token_usage.get("tokens")
            or token_usage.get("total")
            or (prompt + completion)
            or 0
        )
        # If only total was given, distribute it (best effort).
        if total > 0 and prompt == 0 and completion == 0:
            prompt = int(total * 0.7)
            completion = total - prompt
        return {
            "total_tokens": total,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
        }

    @classmethod
    def _estimate_cost(cls, parsed: dict[str, int], model: Optional[str]) -> float:
        """
        Estimate USD cost from token counts and model name.

        Uses a simple per-1M-token rate. Prompt and completion tokens are
        weighted separately (completion is typically 3–5x more expensive).
        Falls back to 'default' if model is unrecognised.
        """
        rate_per_1m = cls._COST_PER_1M_TOKENS["default"]
        if model:
            model_lower = model.lower()
            for prefix, rate in cls._COST_PER_1M_TOKENS.items():
                if prefix != "default" and prefix in model_lower:
                    rate_per_1m = rate
                    break

        # Completion tokens cost ~3x prompt in most pricing models.
        prompt_cost = parsed["prompt_tokens"] * rate_per_1m / 1_000_000
        completion_cost = parsed["completion_tokens"] * rate_per_1m * 3 / 1_000_000
        return round(prompt_cost + completion_cost, 6)

    def get_audit_log(self) -> List[dict]:
        records = self._store().query_records(kind="audit_summary")
        if records:
            return [
                {
                    "event_id": record.id,
                    "timestamp": record.created_at,
                    "project_id": record.project_id or self.get_project_id(),
                    "department": record.department or "orchestrator",
                    "event_type": str((record.metadata or {}).get("event_type") or "event"),
                    "payload_summary": str(record.content),
                    "metadata": dict(record.metadata or {}),
                }
                for record in records
            ]
        raw = self._memory.data.get("audit_log", [])
        if not isinstance(raw, list):
            return []
        return [record for record in raw if isinstance(record, dict)]

    def append_audit_event(
        self,
        *,
        department: str,
        event_type: str,
        payload: Any,
        metadata: Optional[dict] = None,
    ) -> None:
        append_audit_event(
            self._memory,
            project_id=self.get_project_id(),
            department=department,
            event_type=event_type,
            payload=payload,
            metadata=metadata,
        )

    @staticmethod
    def pending_approval_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @classmethod
    def json_safe(cls, value: Any) -> Any:
        if is_dataclass(value):
            return cls.json_safe(asdict(value))
        if isinstance(value, dict):
            return {str(k): cls.json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls.json_safe(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
