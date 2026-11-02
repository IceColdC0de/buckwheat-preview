"""Brand theme constants and Textual CSS definitions for Buckwheat.

Implements the 'Warm Earth & Grain' palette and Buckwheat Sprig emblem.
"""

import importlib
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Brand Palette: Warm Earth & Grain
# ---------------------------------------------------------------------------
GROAT_GOLD = "#E0A96D"  # Primary brand accent, titles, highlights
PARCHMENT = "#EBDCB9"  # Warm cream, subtitles, key UI borders
ROASTED_HUSK = "#8C5835"  # Muted brown, inactive borders
SPROUT_SAGE = "#8F9E8B"  # Success, diff additions (+)
HEARTH_RED = "#C86452"  # Warnings, diff deletions (-)
ROASTED_SOIL = "#201E1C"  # TUI background
TOASTED_SURFACE = "#2B2825"  # Card / container background
PANEL_BG = "#34302C"  # Floating panels and dialogs
MILLED_FLOUR = "#F7F4EE"  # Primary readable text
MUTED_GRAIN = "#A89F91"  # Subtle metadata & captions

PALETTE: dict[str, str] = {
    "groat_gold": GROAT_GOLD,
    "parchment": PARCHMENT,
    "roasted_husk": ROASTED_HUSK,
    "sprout_sage": SPROUT_SAGE,
    "hearth_red": HEARTH_RED,
    "roasted_soil": ROASTED_SOIL,
    "toasted_surface": TOASTED_SURFACE,
    "panel_bg": PANEL_BG,
    "milled_flour": MILLED_FLOUR,
    "muted_grain": MUTED_GRAIN,
}

# ---------------------------------------------------------------------------
# Brand Taglines & Copy
# ---------------------------------------------------------------------------
TAGLINE = "A wholesome grain for your Linux desktop."
SUBTITLE = (
    "Plain-English ricing for Hyprland and friends. "
    "Describe your vibe; Buckwheat prepares the diff."
)

# ---------------------------------------------------------------------------
# Emblem ASCII Artwork (Concept B: The Sprig)
# ---------------------------------------------------------------------------
BANNER_SPRIG_ASCII = """       \\   /
        \\ /      BUCKWHEAT
         |       A wholesome grain for your Linux desktop
        /▲\\
       /▲▲▲\\     Plain-English ricing for Hyprland & Waybar
         |
         |"""

BANNER_COMPACT = "\\v/ BUCKWHEAT"

# Path to the Textual CSS stylesheet
TCSS_PATH = Path(__file__).parent / "buckwheat.tcss"


def get_textual_theme() -> Any:
    """Return a Textual Theme object if textual is installed, or a dict fallback."""
    try:
        # Import lazily so `buckwheat --version` works without loading Textual.
        theme_cls = importlib.import_module("textual.theme").Theme
    except ImportError:
        theme_cls = None
    if theme_cls is not None:
        return theme_cls(
            name="buckwheat",
            primary=GROAT_GOLD,
            secondary=ROASTED_HUSK,
            accent=PARCHMENT,
            warning="#E5A952",
            error=HEARTH_RED,
            success=SPROUT_SAGE,
            background=ROASTED_SOIL,
            surface=TOASTED_SURFACE,
            panel=PANEL_BG,
            dark=True,
        )
    # Fallback dictionary if Textual is not installed in the environment
    return {
        "name": "buckwheat",
        "primary": GROAT_GOLD,
        "secondary": ROASTED_HUSK,
        "accent": PARCHMENT,
        "warning": "#E5A952",
        "error": HEARTH_RED,
        "success": SPROUT_SAGE,
        "background": ROASTED_SOIL,
        "surface": TOASTED_SURFACE,
        "panel": PANEL_BG,
        "dark": True,
    }
