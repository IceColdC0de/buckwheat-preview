"""ChangePlan schema (design.md section 2.2).

Everything is frozen and rejects unknown fields. Ops are a discriminated union
on ``op``. Value rules beyond shape and length live in ``validators.py``.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

TargetName = Literal["kitty", "alacritty", "hyprland", "waybar"]
Scalar = bool | int | float | str  # no lists/dicts in v1


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SetOp(_Op):
    op: Literal["set"]
    target: TargetName
    key: str = Field(max_length=64)  # must be in the adapter's key registry
    value: Scalar


class PaletteOp(_Op):
    op: Literal["palette"]
    palette: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    variant: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,20}$")
    targets: list[TargetName] = Field(min_length=1, max_length=4)


class FontOp(_Op):
    op: Literal["font"]
    target: TargetName
    role: Literal["terminal", "ui", "bar"]
    family: str = Field(min_length=1, max_length=80)
    size: float | None = Field(default=None, ge=6, le=48)


class WallpaperOp(_Op):
    op: Literal["wallpaper"]
    path: str = Field(max_length=512)
    mode: Literal["fill", "fit", "center", "tile"] = "fill"


Op = Annotated[SetOp | PaletteOp | FontOp | WallpaperOp, Field(discriminator="op")]


class ChangePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    summary: str = Field(max_length=200)
    ops: list[Op] = Field(min_length=1, max_length=20)
