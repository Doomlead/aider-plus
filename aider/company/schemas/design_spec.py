from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class DataContract(BaseModel):
    """Defines a single piece of data flowing into or out of a component."""

    field_name: str = Field(
        ..., description="Name of the data field (e.g., 'user_id', 'isLoading')"
    )
    data_type: str = Field(
        ..., description="Type (e.g., 'string', 'boolean', 'Array<User>', 'number')"
    )
    source: Literal["api", "local_state", "global_state", "url_param", "prop", "event"] = Field(
        ..., description="Where this data comes from or lives"
    )
    description: str = Field(..., description="Brief explanation of this data field")
    validation_rules: Optional[list[str]] = Field(
        default_factory=list,
        description=(
            "Validation constraints (e.g., 'required', 'min: 0', 'max_length: 255')"
        ),
    )


class InteractionState(BaseModel):
    """Defines a specific UI state for a component or screen."""

    state_name: str = Field(
        ..., description="Name of the state (e.g., 'loading', 'error', 'empty', 'success')"
    )
    trigger: str = Field(
        ..., description="What causes this state (e.g., 'API call in progress', 'Invalid input')"
    )
    ui_change: str = Field(
        ..., description="What the user sees (e.g., 'Spinner overlay', 'Red border on input')"
    )


class ComponentSpec(BaseModel):
    """A distinct UI component."""

    name: str = Field(..., description="PascalCase component name (e.g., 'AnalyticsDashboard')")
    description: str = Field(..., description="What this component does")
    props: list[DataContract] = Field(..., description="Data passed into this component")
    interaction_states: list[InteractionState] = Field(
        ...,
        description="Must include at least 'default', 'loading', and 'error' states",
    )
    accessibility_notes: Optional[str] = Field(
        default=None,
        description="Aria labels, tab order, screen reader behavior",
    )


class ScreenSpec(BaseModel):
    """A distinct view/page in the application."""

    name: str = Field(
        ..., description="Name of the screen (e.g., 'UserLogin', 'AnalyticsDashboard')"
    )
    route: Optional[str] = Field(default=None, description="URL path (e.g., '/dashboard')")
    description: str = Field(..., description="Purpose of this screen")
    components_used: list[str] = Field(
        ..., description="List of ComponentSpec names rendered here"
    )
    data_fetching: Optional[str] = Field(
        default=None,
        description="How/when data is fetched (e.g., 'On mount via GET /api/analytics')",
    )


class A11yChecklist(BaseModel):
    """Accessibility requirements for the feature."""

    keyboard_navigation: bool = Field(
        default=False,
        description="Can all interactive elements be reached via keyboard?",
    )
    screen_reader_labels: bool = Field(
        default=False,
        description="Do images/buttons have appropriate aria-labels/alt text?",
    )
    color_contrast_aa: bool = Field(
        default=False,
        description="Does text meet WCAG AA contrast ratios?",
    )
    aria_attributes: bool = Field(
        default=False,
        description="Are dynamic regions tagged with aria-live/roles?",
    )
    focus_management: bool = Field(
        default=False,
        description="Is focus moved logically after actions (e.g., modal close)?",
    )
    notes: Optional[str] = Field(default=None)


class DesignSpecV2(BaseModel):
    """The complete, structured UX specification required by Engineering."""

    title: str = Field(..., description="Feature name")
    overview: str = Field(..., description="1-2 sentence summary of what is being built")
    screens: list[ScreenSpec] = Field(
        ..., min_length=1, description="At least one screen is required"
    )
    components: list[ComponentSpec] = Field(
        ..., min_length=1, description="At least one component is required"
    )
    global_state_management: str = Field(
        ...,
        description=(
            "How is state managed globally? (e.g., 'Redux store with userSlice', "
            "'React Context for Auth')"
        ),
    )
    accessibility_checklist: A11yChecklist = Field(
        ..., description="WCAG compliance checklist"
    )
    error_boundaries: Optional[str] = Field(
        default=None,
        description="How are unexpected errors caught and displayed to the user?",
    )
