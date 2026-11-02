import tomllib
from importlib import resources

import pytest

from buckwheat.plan.palettes import (
    Ansi,
    Colors,
    PaletteError,
    list_palettes,
    load_palette,
)

EXPECTED = {
    "nord": {"default"},
    "gruvbox": {"dark", "light"},
    "catppuccin": {"latte", "frappe", "macchiato", "mocha"},
    "dracula": {"default"},
    "tokyo-night": {"night", "storm", "day"},
    "solarized": {"dark", "light"},
    "rose-pine": {"main", "moon", "dawn"},
}


def _variants() -> list[tuple[str, str]]:
    return [(n, v) for n, vs in EXPECTED.items() for v in sorted(vs)]


def test_list_palettes() -> None:
    infos = list_palettes()
    assert {i.name for i in infos} == set(EXPECTED)
    assert [i.name for i in infos] == sorted(i.name for i in infos)
    assert all(i.description for i in infos)


@pytest.mark.parametrize(("name", "variant"), _variants())
def test_every_variant_loads_with_all_slots(name: str, variant: str) -> None:
    pv = load_palette(name, variant)
    assert (pv.palette, pv.variant) == (name, variant)
    colors = pv.colors.model_dump()
    ansi = pv.ansi.model_dump()
    assert set(colors) == set(Colors.model_fields)
    assert set(ansi) == set(Ansi.model_fields)
    assert len(colors) == 12 and len(ansi) == 16


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_file_matches_name_and_variants(name: str) -> None:
    path = resources.files("buckwheat.plan") / "palettes" / f"{name}.toml"
    data = tomllib.loads(path.read_text("utf-8"))
    assert data["name"] == name
    assert set(data["variants"]) == EXPECTED[name]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_default_variant(name: str) -> None:
    assert load_palette(name).variant in EXPECTED[name]


def test_published_reference_values() -> None:
    assert load_palette("catppuccin").colors.bg == "#1e1e2e"  # mocha
    assert load_palette("nord").colors.bg == "#2e3440"
    assert load_palette("dracula").colors.bg == "#282a36"
    assert load_palette("gruvbox", "dark").colors.fg == "#ebdbb2"
    assert load_palette("solarized", "light").colors.bg == "#fdf6e3"
    assert load_palette("tokyo-night").colors.bg == "#1a1b26"
    assert load_palette("rose-pine", "dawn").colors.bg == "#faf4ed"


def test_unknown_palette() -> None:
    with pytest.raises(PaletteError, match="unknown palette 'nope'.*nord"):
        load_palette("nope")


def test_unknown_variant() -> None:
    with pytest.raises(PaletteError, match="unknown variant 'neon'.*mocha"):
        load_palette("catppuccin", "neon")


def test_variant_on_single_variant_palette() -> None:
    with pytest.raises(PaletteError, match="available: default"):
        load_palette("nord", "dark")


def test_path_like_name_rejected() -> None:
    with pytest.raises(PaletteError):
        load_palette("../config")
