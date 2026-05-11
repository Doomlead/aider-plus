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
        rejection_reasons: Optional[list[str]] = None,
    ):
        self.approved = approved
        self.parsed_spec = parsed_spec
        self.rejection_reasons = rejection_reasons or []

    def to_engineering_rejection_payload(self) -> str:
        reasons_text = "\n".join(f"• {r}" for r in self.rejection_reasons)
        return (
            "**Engineering Schema Gate REJECTION**\n\n"
            "The DesignSpec failed validation and cannot be handed off.\n\n"
            f"**Issues to fix:**\n{reasons_text}\n\n"
            "Please regenerate a complete, valid DesignSpecV2."
        )


class SchemaGateValidator:
    """Enforces Engineering's minimum requirements on UX output."""

    def validate(self, ux_output: dict | str | None) -> GateResult:
        if not ux_output:
            return GateResult(approved=False, rejection_reasons=["No UX output received"])

        if isinstance(ux_output, str):
            try:
                ux_output = json.loads(ux_output)
            except json.JSONDecodeError:
                return GateResult(
                    approved=False,
                    rejection_reasons=["UX output must be valid JSON"],
                )

        if not isinstance(ux_output, dict):
            return GateResult(
                approved=False,
                rejection_reasons=["UX output must be a JSON object"],
            )

        try:
            spec = DesignSpecV2.model_validate(ux_output)
        except ValidationError as e:
            reasons = [
                f"Field '{'.'.join(str(x) for x in err['loc'])}': {err['msg']}"
                for err in e.errors()
            ]
            return GateResult(approved=False, rejection_reasons=reasons)

        semantic_errors = []
        component_names = {component.name.lower() for component in spec.components}

        for comp in spec.components:
            states = {state.state_name.lower() for state in comp.interaction_states}
            if not any(state in states for state in ("loading", "pending")):
                semantic_errors.append(f"Component '{comp.name}' is missing a loading state")
            if not any(state in states for state in ("error", "failed")):
                semantic_errors.append(f"Component '{comp.name}' is missing an error state")

        for screen in spec.screens:
            for comp_name in screen.components_used:
                if comp_name.lower() not in component_names:
                    semantic_errors.append(
                        f"Screen '{screen.name}' references undefined component '{comp_name}'"
                    )

        if semantic_errors:
            return GateResult(
                approved=False,
                parsed_spec=spec,
                rejection_reasons=semantic_errors,
            )

        logger.info("Schema Gate: DesignSpecV2 passed validation")
        return GateResult(approved=True, parsed_spec=spec)
