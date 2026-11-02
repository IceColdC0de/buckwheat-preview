"""Planning context and validation problems (design.md sections 1 and 2.3)."""

from pydantic import BaseModel, ConfigDict, Field

from buckwheat.plan.models import Scalar, TargetName


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Problem(_Frozen):
    """One rejection of a plan op, shown to the user and fed back on retry."""

    op_index: int | None = Field(default=None, ge=0)  # None: plan-level problem
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(max_length=500)


class TargetInfo(_Frozen):
    name: TargetName
    detected: bool
    version: str | None = None
    managed_installed: bool = False
    current_values: dict[str, Scalar] = Field(default_factory=dict)
    shadowed_keys: list[str] = Field(default_factory=list)


class PaletteInfo(_Frozen):
    """Palette name and one-line description only; never colour values."""

    name: str
    description: str = ""


class PlanLimits(_Frozen):
    """What the planner may use: allowed ops and settable keys per target."""

    ops: list[str] = Field(default_factory=list)
    keys: dict[TargetName, list[str]] = Field(default_factory=dict)
    # Optional compact, typed description of a target's keys, shown to the
    # planner instead of the bare key list (Hyprland has hundreds of keys).
    key_notes: dict[TargetName, str] = Field(default_factory=dict)


class PlanContext(_Frozen):
    request_id: str
    targets: list[TargetInfo] = Field(default_factory=list)
    palettes: list[PaletteInfo] = Field(default_factory=list)
    limits: PlanLimits = Field(default_factory=PlanLimits)
