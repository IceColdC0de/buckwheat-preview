"""Palette library: load and validate palette TOML files."""

import tomllib
from importlib import resources
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from buckwheat.plan.context import PaletteInfo

Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-f]{6}$")]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+$")]


class PaletteError(ValueError):
    """Unknown palette or variant, or a malformed palette file."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Colors(_Frozen):
    bg: Hex
    bg_alt: Hex
    surface: Hex
    fg: Hex
    fg_dim: Hex
    accent: Hex
    accent_alt: Hex
    warning: Hex
    error: Hex
    cursor: Hex
    selection_bg: Hex
    selection_fg: Hex


class Ansi(_Frozen):
    black: Hex
    red: Hex
    green: Hex
    yellow: Hex
    blue: Hex
    magenta: Hex
    cyan: Hex
    white: Hex
    bright_black: Hex
    bright_red: Hex
    bright_green: Hex
    bright_yellow: Hex
    bright_blue: Hex
    bright_magenta: Hex
    bright_cyan: Hex
    bright_white: Hex


class Variant(_Frozen):
    dark: bool
    colors: Colors
    ansi: Ansi


class Palette(_Frozen):
    """One palette family as stored in a TOML file."""

    name: Slug
    description: str = Field(max_length=200)
    default_variant: Slug
    variants: dict[Slug, Variant] = Field(min_length=1)

    @model_validator(mode="after")
    def _default_exists(self) -> Self:
        if self.default_variant not in self.variants:
            raise ValueError(f"default_variant {self.default_variant!r} is unknown")
        return self


class PaletteVariant(_Frozen):
    """A resolved palette variant, ready for adapter slot maps."""

    palette: str
    variant: str
    dark: bool
    colors: Colors
    ansi: Ansi


def _package_files() -> list[str]:
    root = resources.files("buckwheat.plan") / "palettes"
    return sorted(
        p.name.removesuffix(".toml") for p in root.iterdir() if p.name.endswith(".toml")
    )


def _load_family(name: str) -> Palette:
    if name not in _package_files():
        known = ", ".join(_package_files())
        raise PaletteError(f"unknown palette {name!r}; available: {known}")
    path = resources.files("buckwheat.plan") / "palettes" / f"{name}.toml"
    try:
        palette = Palette.model_validate(tomllib.loads(path.read_text("utf-8")))
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise PaletteError(f"invalid palette file {name}.toml: {exc}") from exc
    if palette.name != name:
        raise PaletteError(f"palette file {name}.toml declares name {palette.name!r}")
    return palette


def load_palette(name: str, variant: str | None = None) -> PaletteVariant:
    """Return one variant of a palette; `variant=None` means its default."""
    family = _load_family(name)
    chosen = variant if variant is not None else family.default_variant
    if chosen not in family.variants:
        known = ", ".join(sorted(family.variants))
        raise PaletteError(
            f"unknown variant {chosen!r} for palette {name!r}; available: {known}"
        )
    v = family.variants[chosen]
    return PaletteVariant(
        palette=name, variant=chosen, dark=v.dark, colors=v.colors, ansi=v.ansi
    )


def list_palettes() -> list[PaletteInfo]:
    """Names and descriptions of all palettes, sorted by name."""
    return [
        PaletteInfo(name=n, description=_load_family(n).description)
        for n in _package_files()
    ]
