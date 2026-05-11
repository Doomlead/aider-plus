from __future__ import annotations

import json

from aider.company.validators.schema_gate import SchemaGateValidator


def valid_design_spec() -> dict:
    return {
        "title": "Invite teammates",
        "overview": "A focused invite flow that keeps progress and recovery visible.",
        "screens": [
            {
                "name": "InviteScreen",
                "route": "/settings/team/invites",
                "description": "Lets admins send invitations and review pending invites.",
                "components_used": ["InviteForm", "InviteStatus", "InviteErrorState"],
                "data_fetching": "Load pending invites from the team invites API.",
            }
        ],
        "components": [
            {
                "name": "InviteForm",
                "description": "Collects invitee email and role before submission.",
                "props": [
                    {
                        "field_name": "email",
                        "data_type": "string",
                        "source": "local_state",
                        "description": "Invitee email address.",
                        "validation_rules": ["Must be a valid email address"],
                    }
                ],
                "interaction_states": [
                    {
                        "state_name": "loading",
                        "trigger": "Invite submission starts",
                        "ui_change": "Disable submit and show spinner text.",
                    },
                    {
                        "state_name": "error",
                        "trigger": "Invite submission fails",
                        "ui_change": "Show inline validation and API error feedback.",
                    },
                ],
                "accessibility_notes": "Labels are programmatically associated with inputs.",
            },
            {
                "name": "InviteStatus",
                "description": "Shows pending and sent invitation status.",
                "props": [],
                "interaction_states": [
                    {
                        "state_name": "pending",
                        "trigger": "Status refresh starts",
                        "ui_change": "Show skeleton rows.",
                    },
                    {
                        "state_name": "failed",
                        "trigger": "Status refresh fails",
                        "ui_change": "Show retry action.",
                    },
                ],
                "accessibility_notes": "Status updates use polite live regions.",
            },
            {
                "name": "InviteErrorState",
                "description": "Provides recovery guidance for empty or failed states.",
                "props": [],
                "interaction_states": [
                    {
                        "state_name": "loading",
                        "trigger": "Retry starts",
                        "ui_change": "Announce retry progress.",
                    },
                    {
                        "state_name": "error",
                        "trigger": "Retry fails",
                        "ui_change": "Keep retry controls visible.",
                    },
                ],
                "accessibility_notes": "Recovery controls are keyboard accessible.",
            },
        ],
        "global_state_management": "Keep form state local and cache pending invites globally.",
        "accessibility_checklist": {
            "keyboard_navigation": True,
            "screen_reader_labels": True,
            "color_contrast_aa": True,
            "aria_attributes": True,
            "focus_management": True,
            "notes": "Meets WCAG 2.1 AA for labels, focus, and contrast.",
        },
        "error_boundaries": "Wrap invite list and form submission in recoverable boundaries.",
    }


def test_schema_gate_accepts_valid_design_spec_dict():
    result = SchemaGateValidator().validate(valid_design_spec())

    assert result.approved is True
    assert result.parsed_spec is not None
    assert result.rejection_reasons == []


def test_schema_gate_accepts_valid_design_spec_json():
    result = SchemaGateValidator().validate(json.dumps(valid_design_spec()))

    assert result.approved is True
    assert result.parsed_spec is not None


def test_schema_gate_rejects_invalid_json():
    result = SchemaGateValidator().validate("not json")

    assert result.approved is False
    assert result.rejection_reasons == ["UX output must be valid JSON"]


def test_schema_gate_rejects_missing_component_states_and_bad_screen_reference():
    spec = valid_design_spec()
    spec["screens"][0]["components_used"].append("MissingComponent")
    spec["components"][0]["interaction_states"] = []

    result = SchemaGateValidator().validate(spec)

    assert result.approved is False
    assert "Component 'InviteForm' is missing a loading state" in result.rejection_reasons
    assert "Component 'InviteForm' is missing an error state" in result.rejection_reasons
    assert (
        "Screen 'InviteScreen' references undefined component 'MissingComponent'"
        in result.rejection_reasons
    )


def test_schema_gate_rejects_unexpected_fields():
    spec = valid_design_spec()
    spec["unexpected"] = "not allowed"

    result = SchemaGateValidator().validate(spec)

    assert result.approved is False
    assert "Field 'unexpected': Extra inputs are not permitted" in result.rejection_reasons
