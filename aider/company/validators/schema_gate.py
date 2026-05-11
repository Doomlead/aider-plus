from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import ValidationError

from aider.company.schemas.design_spec import DesignSpecV2

logger = logging.getLogger(__name__)


class GateResult:
    def __init__(
        self,
        approved: bool,
        parsed_spec: Optional[DesignSpecV2] = None,
        rejection_reasons: list[str] | None = None,
    ):
        self.approved = approved
        self.parsed_spec = parsed_spec
        self.rejection_reasons = rejection_reasons or []

    def to_engineering_rejection_payload(self) -> str:
        """Formats the rejection into a clear prompt for the UX agent to fix."""
        reasons_text = "\n".join(f"- {r}" for r in self.rejection_reasons)
        return (
            "Engineering Schema Gate REJECTION.\n"
            "The DesignSpec was rejected due to missing or invalid fields.\n"
            "Please fix the following issues and regenerate the UX deliverable:\n\n"
            f"{reasons_text}"
        )


class SchemaGateValidator:
    """Validates that a UX deliverable meets Engineering's intake requirements."""

    def validate(self, ux_output: dict | str) -> GateResult:
        """Runs structural and semantic validation against DesignSpecV2."""

        if isinstance(ux_output, str):
            try:
                ux_output = json.loads(ux_output)
            except json.JSONDecodeError:
                return GateResult(
                    approved=False,
                    rejection_reasons=[
                        "UX output is not valid JSON. Must be a structured object."
                    ],
                )

        if not isinstance(ux_output, dict):
            return GateResult(
                approved=False,
                rejection_reasons=[
                    "UX output must be a JSON object/dict, not a plain string."
                ],
            )

        try:
            spec = DesignSpecV2(**ux_output)
        except ValidationError as err:
            reasons = []
            for error in err.errors():
                loc = " -> ".join(str(part) for part in error["loc"])
                reasons.append(f"Field '{loc}': {error['msg']}")
            return GateResult(approved=False, rejection_reasons=reasons)

        semantic_errors = []

        for comp in spec.components:
            state_names = [state.state_name.lower() for state in comp.interaction_states]
            if "loading" not in state_names and "pending" not in state_names:
                semantic_errors.append(
                    f"Component '{comp.name}' is missing a 'loading' interaction state."
                )
            if "error" not in state_names and "failed" not in state_names:
                semantic_errors.append(
                    f"Component '{comp.name}' is missing an 'error' interaction state."
                )
            if not comp.props:
                semantic_errors.append(
                    f"Component '{comp.name}' has no props defined. "
                    "Even pure components need explicit empty props."
                )

        all_component_names = {component.name.lower() for component in spec.components}
        for screen in spec.screens:
            for comp_ref in screen.components_used:
                if comp_ref.lower() not in all_component_names:
                    semantic_errors.append(
                        f"Screen '{screen.name}' references component '{comp_ref}', but it is "
                        "not defined in the components list."
                    )

        if semantic_errors:
            return GateResult(
                approved=False,
                parsed_spec=spec,
                rejection_reasons=semantic_errors,
            )

        logger.info("Schema Gate: DesignSpecV2 approved successfully.")
        return GateResult(approved=True, parsed_spec=spec)
