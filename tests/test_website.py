"""Unit tests for the Buckwheat static website assets and branding compliance."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBSITE_DIR = ROOT / "website"


def test_website_files_exist() -> None:
    """Verify core website files are present."""
    assert (WEBSITE_DIR / "index.html").is_file()
    assert (WEBSITE_DIR / "style.css").is_file()
    assert (WEBSITE_DIR / "app.js").is_file()
    assert (WEBSITE_DIR / "assets" / "sprig.svg").is_file()
    assert (WEBSITE_DIR / "assets" / "favicon.svg").is_file()
    assert (WEBSITE_DIR / "vercel.json").is_file()
    assert (ROOT / "vercel.json").is_file()


def test_index_html_branding_and_semantics() -> None:
    """Verify index.html contains required semantic tags and brand copy."""
    content = (WEBSITE_DIR / "index.html").read_text(encoding="utf-8")

    # Semantic tags
    assert "<header" in content
    assert "<nav" in content
    assert "<main" in content
    assert "<section" in content
    assert "<footer" in content

    # Official copy from BRANDING.md
    assert "A wholesome grain for your Linux desktop" in content
    assert "Plain-English ricing for Hyprland and friends" in content
    assert "What vibe are we cooking up today?" in content
    assert "Freshly prepared diff" in content
    assert "Testing new look (auto-reverting in" in content
    assert "Keep changes [Enter]" in content
    assert "Revert to previous [Esc]" in content

    # The 4 Guardrails
    assert "Managed Overlays, Not Overwrites" in content
    assert "Inspect Before You Apply" in content
    assert "Timed Revert Safety Net" in content
    assert "Structured Changes" in content


def test_css_brand_palette_tokens() -> None:
    """Verify CSS variables match BRANDING.md hex codes exactly."""
    css = (WEBSITE_DIR / "style.css").read_text(encoding="utf-8")
    expected_tokens = {
        "--groat-accent": "#E0A96D",
        "--parchment": "#EBDCB9",
        "--husk-dim": "#8C5835",
        "--sage-green": "#8F9E8B",
        "--ember-red": "#C86452",
        "--soil-bg": "#201E1C",
        "--flour-fg": "#F7F4EE",
    }
    css_lower = css.lower()
    for token, hex_val in expected_tokens.items():
        found = (f"{token}: {hex_val}" in css) or (
            f"{token}: {hex_val.lower()}" in css_lower
        )
        assert found


def test_app_js_features() -> None:
    """Verify JavaScript includes interactive logic and safety timer."""
    js = (WEBSITE_DIR / "app.js").read_text(encoding="utf-8")
    assert "PRESETS" in js
    assert "revertCountdown = 30" in js
    assert "autumn" in js
    assert "arctic" in js
    assert '"Escape"' in js
    assert '"Enter"' in js


def test_vercel_configurations() -> None:
    """Verify both website/vercel.json and root vercel.json are valid."""
    root_cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert root_cfg.get("outputDirectory") == "website"
    assert root_cfg.get("cleanUrls") is True

    web_cfg = json.loads((WEBSITE_DIR / "vercel.json").read_text(encoding="utf-8"))
    assert web_cfg.get("cleanUrls") is True
