"""Tests for Buckwheat branding theme and Textual assets."""

import unittest

from buckwheat.tui.theme import (
    BANNER_COMPACT,
    BANNER_SPRIG_ASCII,
    GROAT_GOLD,
    HEARTH_RED,
    PALETTE,
    PARCHMENT,
    ROASTED_SOIL,
    SPROUT_SAGE,
    SUBTITLE,
    TAGLINE,
    TCSS_PATH,
    get_textual_theme,
)


class TestBrandTheme(unittest.TestCase):
    """Verify brand palette, copy, and TCSS definitions."""

    def test_palette_hex_codes(self):
        """Ensure all palette colors match valid 6-character hex format."""
        for name, hex_val in PALETTE.items():
            self.assertTrue(
                hex_val.startswith("#") and len(hex_val) == 7,
                f"Color '{name}' has invalid hex '{hex_val}'",
            )

    def test_exact_brand_colors(self):
        """Validate key brand color tokens against the specification."""
        self.assertEqual(GROAT_GOLD, "#E0A96D")
        self.assertEqual(PARCHMENT, "#EBDCB9")
        self.assertEqual(ROASTED_SOIL, "#201E1C")
        self.assertEqual(SPROUT_SAGE, "#8F9E8B")
        self.assertEqual(HEARTH_RED, "#C86452")

    def test_tagline_and_subtitle(self):
        """Verify taglines match approved brand copy."""
        self.assertEqual(TAGLINE, "A wholesome grain for your Linux desktop.")
        self.assertIn("Plain-English ricing", SUBTITLE)
        self.assertNotIn("AI", SUBTITLE)

    def test_banner_ascii(self):
        """Verify ASCII emblem banner has Buckwheat branding and Sprig shape."""
        self.assertIn("BUCKWHEAT", BANNER_SPRIG_ASCII)
        self.assertIn("▲", BANNER_SPRIG_ASCII)
        self.assertEqual(BANNER_COMPACT, "\\v/ BUCKWHEAT")

    def test_tcss_stylesheet_exists_and_valid(self):
        """Ensure the Textual CSS file exists and declares design tokens."""
        self.assertTrue(TCSS_PATH.is_file(), f"TCSS file not found at {TCSS_PATH}")
        content = TCSS_PATH.read_text(encoding="utf-8")
        self.assertIn("$groat-gold: #E0A96D;", content)
        self.assertIn("$roasted-soil: #201E1C;", content)
        self.assertIn("#brand-emblem", content)
        self.assertIn("#revert-bar", content)

    def test_get_textual_theme(self):
        """Ensure get_textual_theme returns a valid theme mapping or object."""
        theme = get_textual_theme()
        if isinstance(theme, dict):
            self.assertEqual(theme["name"], "buckwheat")
            self.assertEqual(theme["primary"], GROAT_GOLD)
            self.assertEqual(theme["background"], ROASTED_SOIL)
        else:
            self.assertEqual(theme.name, "buckwheat")


if __name__ == "__main__":
    unittest.main()
