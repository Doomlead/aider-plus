"""
Audit log pattern extractor for post-mortem learning.

Reads structured audit log records and extracts typed patterns that can
be stored in the playbook for future context injection.

Pattern types:
  - qa_failure      → coding_standards (what caused tests to fail)
  - ceo_rejection   → ux_preferences (what the CEO pushed back on)
  - deploy_failure  → deployment_gotchas (what broke at deploy time)
  - eng_revision    → coding_standards (what the internal reviewer flagged)

Each pattern is a dict so it carries provenance (project, timestamp, type)
in addition to the human-readable text. The playbook stores these dicts;
the old string format is handled by migration.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Pattern schema
# ---------------------------------------------------------------------------


def make_pattern(
    *,
    text: str,
    pattern_type: str,
    project_name: str = "",
    project_id: str = "",
    timestamp: str = "",
    source_event: str = "",
) -> dict:
    """
    Return a canonical pattern dict for playbook storage.

    The *text* field is what gets injected into prompts and scored by
    MemoryRetriever. All other fields are provenance metadata.
    """
    return {
        "text": str(text)[:500],
        "pattern_type": pattern_type,
        "project_name": project_name,
        "project_id": project_id,
        "timestamp": timestamp,
        "source_event": source_event,
    }


def pattern_text(entry: Any) -> str:
    """Extract the injectable text from a pattern entry (dict or legacy str)."""
    if isinstance(entry, dict):
        return str(entry.get("text", entry))
    return str(entry)


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class AuditPatternExtractor:
    """
    Derives playbook patterns from a list of audit log records.

    Usage:
        extractor = AuditPatternExtractor(audit_records, project_name, project_id)
        patterns = extractor.extract()
        # patterns = {
        #     "coding_standards": [pattern_dict, ...],
        #     "ux_preferences":   [pattern_dict, ...],
        #     "deployment_gotchas": [pattern_dict, ...],
        # }
    """

    def __init__(
        self,
        audit_records: list[dict],
        project_name: str = "",
        project_id: str = "",
    ):
        self._records = [r for r in audit_records if isinstance(r, dict)]
        self._project_name = project_name
        self._project_id = project_id

    def extract(self) -> dict[str, list[dict]]:
        """Return extracted patterns grouped by playbook category."""
        result: dict[str, list[dict]] = {
            "coding_standards": [],
            "ux_preferences": [],
            "deployment_gotchas": [],
        }
        for record in self._records:
            event_type = record.get("event_type", "")
            metadata = record.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            timestamp = record.get("timestamp", "")
            payload_summary = record.get("payload_summary", "")

            if event_type == "qa_fail":
                text = self._qa_failure_text(metadata, payload_summary)
                if text:
                    result["coding_standards"].append(
                        make_pattern(
                            text=text,
                            pattern_type="qa_failure",
                            project_name=self._project_name,
                            project_id=self._project_id,
                            timestamp=timestamp,
                            source_event=event_type,
                        )
                    )

            elif (
                event_type == "approval_resolved"
                and metadata.get("approved") is False
            ):
                text = self._rejection_text(metadata)
                if text:
                    result["ux_preferences"].append(
                        make_pattern(
                            text=text,
                            pattern_type="ceo_rejection",
                            project_name=self._project_name,
                            project_id=self._project_id,
                            timestamp=timestamp,
                            source_event=event_type,
                        )
                    )

            elif event_type == "deployment_failure":
                text = self._deployment_failure_text(metadata, payload_summary)
                if text:
                    result["deployment_gotchas"].append(
                        make_pattern(
                            text=text,
                            pattern_type="deploy_failure",
                            project_name=self._project_name,
                            project_id=self._project_id,
                            timestamp=timestamp,
                            source_event=event_type,
                        )
                    )

            elif event_type == "engineering_revision_needed":
                text = self._engineering_revision_text(metadata)
                if text:
                    result["coding_standards"].append(
                        make_pattern(
                            text=text,
                            pattern_type="eng_revision",
                            project_name=self._project_name,
                            project_id=self._project_id,
                            timestamp=timestamp,
                            source_event=event_type,
                        )
                    )

        return result

    # ------------------------------------------------------------------
    # Text extractors per event type
    # ------------------------------------------------------------------

    def _qa_failure_text(self, metadata: dict, payload_summary: str) -> str:
        failed_tests = metadata.get("failed_tests") or []
        if isinstance(failed_tests, list) and failed_tests:
            tests_str = ", ".join(str(t) for t in failed_tests[:3])
            return (
                f"QA failed in project '{self._project_name}': "
                f"tests [{tests_str}] failed. "
                f"Summary: {str(payload_summary)[:200]}"
            )
        if payload_summary:
            return (
                f"QA failed in project '{self._project_name}': "
                f"{str(payload_summary)[:300]}"
            )
        return ""

    def _rejection_text(self, metadata: dict) -> str:
        feedback = (
            metadata.get("feedback")
            or metadata.get("reason")
            or metadata.get("ceo_feedback")
        )
        if not feedback:
            return ""
        gate = metadata.get("gate_name", "approval")
        return (
            f"CEO rejected {gate} in project '{self._project_name}': "
            f"{str(feedback)[:300]}"
        )

    def _deployment_failure_text(self, metadata: dict, payload_summary: str) -> str:
        detail = metadata.get("error") or metadata.get("reason") or payload_summary
        if not detail:
            return ""
        return (
            f"Deployment failed in project '{self._project_name}': "
            f"{str(detail)[:300]}"
        )

    def _engineering_revision_text(self, metadata: dict) -> str:
        summary = (
            metadata.get("reviewer_feedback_summary")
            or metadata.get("last_reviewer_issues")
            or metadata.get("feedback")
        )
        if not summary:
            return ""
        return (
            f"Engineering reviewer flagged in project '{self._project_name}': "
            f"{str(summary)[:300]}"
        )
