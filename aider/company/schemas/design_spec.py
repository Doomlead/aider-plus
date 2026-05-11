from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictDesignBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataContract(StrictDesignBaseModel):
    field_name: str = Field(..., description="Name of the data field")
    data_type: str = Field(..., description="Type (string, boolean, Array<User>, etc.)")
    source: Literal["api", "local_state", "global_state", "url_param", "prop", "event"] = Field(...)
    description: str = Field(...)
    validation_rules: list[str] | None = Field(default_factory=list)


class InteractionState(StrictDesignBaseModel):
    state_name: str = Field(...)
    trigger: str = Field(...)
    ui_change: str = Field(...)


class ComponentSpec(StrictDesignBaseModel):
    name: str = Field(...)
    description: str = Field(...)
    props: list[DataContract] = Field(...)
    interaction_states: list[InteractionState] = Field(...)
    accessibility_notes: str | None = None


class ScreenSpec(StrictDesignBaseModel):
    name: str = Field(...)
    route: str | None = None
    description: str = Field(...)
    components_used: list[str] = Field(...)
    data_fetching: str | None = None


class A11yChecklist(StrictDesignBaseModel):
    keyboard_navigation: bool = False
    screen_reader_labels: bool = False
    color_contrast_aa: bool = False
    aria_attributes: bool = False
    focus_management: bool = False
    notes: str | None = None


class DesignSpecV2(StrictDesignBaseModel):
    """Structured UX deliverable required by Engineering."""

    title: str = Field(...)
    overview: str = Field(...)
    screens: list[ScreenSpec] = Field(..., min_length=1)
    components: list[ComponentSpec] = Field(..., min_length=1)
    global_state_management: str = Field(...)
    accessibility_checklist: A11yChecklist = Field(...)
    error_boundaries: str | None = None

    def to_markdown(self) -> str:
        def _bullet(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "- None"

        screens = _bullet(
            [
                f"{screen.name} ({screen.route or 'no route'}): {screen.description}"
                for screen in self.screens
            ]
        )
        components = _bullet(
            [f"{component.name}: {component.description}" for component in self.components]
        )
        contracts = _bullet(
            [
                f"{component.name}.{prop.field_name} [{prop.source}]: "
                f"{prop.data_type} — {prop.description}"
                for component in self.components
                for prop in component.props
            ]
        )
        return (
            f"# Design Spec: {self.title}\n\n"
            f"## Overview\n{self.overview}\n\n"
            f"## Screens\n{screens}\n\n"
            f"## Components\n{components}\n\n"
            f"## Data Contracts\n{contracts}\n\n"
            f"## Global State Management\n{self.global_state_management}\n\n"
            "## Accessibility Checklist\n"
            f"```json\n{json.dumps(self.accessibility_checklist.model_dump(), indent=2)}\n```\n\n"
            f"## Error Boundaries\n{self.error_boundaries or 'None specified'}\n"
        )
