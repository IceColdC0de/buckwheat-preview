"""The hard line (owner decision 2026-11-01): nothing buckwheat writes may run
programs or reach the network. ``safety.content.check_generated`` refuses it,
and the session runs that check on every generated file before the preview.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from buckwheat.adapters.base import CommandError, FakeRunner, RenderResult
from buckwheat.adapters.hyprland import HyprlandAdapter
from buckwheat.config import SafetySettings, Settings
from buckwheat.plan import ChangePlan
from buckwheat.providers.fake import FakeProvider
from buckwheat.safety.content import UnsafeContentError, check_generated
from buckwheat.session import PlanRejectedError, Session

HOME = Path("/home/u")
FIXTURES = Path(__file__).parent / "fixtures"


def ok(name: str, text: str) -> None:
    check_generated(HOME / ".config" / name, text, home=HOME)


def refused(name: str, text: str) -> str:
    with pytest.raises(UnsafeContentError) as info:
        check_generated(HOME / ".config" / name, text, home=HOME)
    return str(info.value)


# --- Lua ---------------------------------------------------------------------

LUA_OK = """\
-- Managed by buckwheat. Edits are overwritten.
hl.config({
    general = { col = { active_border = { colors = { "rgb(88c0d0)" }, angle = 4 } } },
    group = { groupbar = { font_family = "Load Sans" } },
})
"""


def test_lua_plain_config_passes() -> None:
    ok("hypr/buckwheat.lua", LUA_OK)


@pytest.mark.parametrize(
    "line",
    [
        'hl.exec_cmd("kitty")',
        'hl.on("hyprland.start", function() end)',
        'hl.bind("SUPER + Q", hl.dsp.exec_cmd("kitty"))',
        "hl.timer(1, nil)",
        'hl.config({ exec_once = "x" })',
        'require("other")',
        'os.execute("x")',
        'io.open("/etc/passwd")',
        'dofile("/tmp/x.lua")',
        "load(string.char(65))()",
        "pcall(print)",
        "print('hi')",
        "local s = [[ x ]]",
        "hl . exec_cmd('x')",
        'hl.config({ a = "x" }) hl.dispatch("y")',
        "hl.config{ a = hl.something }",
        "_G.x = 1",
        'hl.config({ general = { layout = "hl.exec_cmd" } })',
    ],
)
def test_lua_that_runs_things_is_refused(line: str) -> None:
    refused("hypr/buckwheat.lua", line + "\n")


def test_lua_strings_and_comments_do_not_hide_calls() -> None:
    # A call after a string that tried to close itself early.
    refused("hypr/buckwheat.lua", 'hl.config({ a = "x\\"" }) print("y")\n')
    # A comment line is ignored, the code after it is not.
    refused("hypr/buckwheat.lua", "-- fine\nprint(1)\n")


# --- classic configs ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "exec-once = kitty\n",
        "  exec = waybar\n",
        "bind = SUPER, Q, exec, kitty\n",
        "source = /tmp/evil.conf\n",
        "plugin = /tmp/x.so\n",
        "permission = x, screencopy, allow\n",
        "map ctrl+a launch sh\n",
        "shell /bin/sh -c evil\n",
        "startup_session x\n",
        "geninclude x.py\n",
        "general {\n    gaps_in = $(id)\n}\n",
    ],
)
def test_conf_that_runs_things_is_refused(text: str) -> None:
    refused("hypr/buckwheat.conf", text)


def test_existing_golden_files_pass() -> None:
    """Every golden file the adapters generate today is fine."""
    goldens = [
        ("hypr/buckwheat.conf", FIXTURES / "hyprland" / "set_ops.golden"),
        ("hypr/buckwheat.conf", FIXTURES / "hyprland" / "palette_nord.golden"),
        ("hypr/buckwheat.lua", FIXTURES / "hyprland" / "all_keys.lua.golden"),
        ("hypr/buckwheat.lua", FIXTURES / "hyprland" / "visual.lua.golden"),
        ("kitty/buckwheat.conf", FIXTURES / "kitty" / "golden_buckwheat.conf"),
        ("hypr/hyprpaper.conf", FIXTURES / "wallpaper" / "golden_fill.conf"),
        ("waybar/buckwheat.css", FIXTURES / "waybar" / "golden" / "buckwheat.css"),
        ("waybar/buckwheat.jsonc", FIXTURES / "waybar" / "golden" / "buckwheat.jsonc"),
        ("alacritty/buckwheat.toml", FIXTURES / "alacritty" / "golden_nord_font.toml"),
    ]
    for name, path in goldens:
        ok(name, path.read_text())


# --- TOML --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '[terminal.shell]\nprogram = "/bin/sh"\n',
        '[[hints.enabled]]\ncommand = "xdg-open"\n',
        '[[keyboard.bindings]]\nkey = "N"\n',
        'import = ["/tmp/x.toml"]\n',
    ],
)
def test_toml_that_runs_things_is_refused(text: str) -> None:
    refused("alacritty/buckwheat.toml", text)


# --- Waybar ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{ "modules-left": ["custom/x"] }',
        '{ "custom/x": { "format": "a" } }',
        '{ "clock": { "on-click": "kitty" } }',
        '{ "clock": { "on-scroll-up": "x" } }',
        '{ "battery": { "exec": "x" } }',
        '{ "battery": { "exec-if": "x" } }',
        '{ "include": ["/tmp/x"] }',
    ],
)
def test_waybar_config_that_runs_things_is_refused(text: str) -> None:
    refused("waybar/buckwheat.jsonc", text)


def test_waybar_plain_config_passes() -> None:
    ok("waybar/buckwheat.jsonc", '{ "position": "top", "height": 30 }')


# --- CSS ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "css",
    [
        "window { background-image: url(https://example.com/a.png); }",
        "window { background-image: url('http://example.com/a.png'); }",
        'window { background-image: url("//example.com/a.png"); }',
        "window { background-image: url(data:image/png;base64,AAAA); }",
        "window { background-image: url(/etc/passwd); }",
        "window { background-image: url(../../../../etc/x.png); }",
        "window { background-image: url(file:///usr/share/x.png); }",
        "window { background-image: URL ( 'https://x' ); }",
        '@import "https://example.com/x.css";',
        "@import url(theme.css);",
        "window { background-image: u\\72l(https://x); }",
    ],
)
def test_css_reaching_outside_is_refused(css: str) -> None:
    refused("waybar/buckwheat.css", css)


@pytest.mark.parametrize(
    "css",
    [
        "window { background-image: url('/home/u/Pictures/bar.png'); }",
        "window { background-image: url(file:///home/u/Pictures/bar.png); }",
        'window { background-image: url("~/Pictures/bar.png"); }',
        "window { background-image: url(img/bar.png); }",
        "window#waybar { background: alpha(@bg, 0.8); border-radius: 8px; }",
    ],
)
def test_css_with_own_files_passes(css: str) -> None:
    ok("waybar/buckwheat.css", css)


# --- the session runs the check ---------------------------------------------


class EvilAdapter(HyprlandAdapter):
    """A broken adapter that renders a program start into its managed file."""

    def render(self, ops, state):  # type: ignore[no-untyped-def]
        return RenderResult({self.lua_managed_path: 'hl.exec_cmd("kitty")\n'})


def test_session_refuses_unsafe_generated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    conf = home / ".config" / "hypr"
    conf.mkdir(parents=True)
    (conf / "hyprland.lua").write_text("hl.config({})\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    if os.geteuid() == 0:
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
    adapter = EvilAdapter(
        FakeRunner({("hyprctl", "version"): CommandError("no")}), config_dir=conf
    )
    adapter.managed_files = [adapter.lua_managed_path]  # type: ignore[misc]
    plan = ChangePlan.model_validate(
        {
            "version": 1,
            "summary": "t",
            "ops": [
                {
                    "op": "set",
                    "target": "hyprland",
                    "key": "general.gaps_in",
                    "value": 4,
                }
            ],
        }
    )
    session = Session(
        FakeProvider(default=plan),
        Settings(safety=SafetySettings(max_retries=0)),
        {"hyprland": adapter},
        home=home,
        state_path=home / "state.json",
    )
    with pytest.raises(PlanRejectedError) as info:
        session.propose("gaps")
    assert "unsafe-content" in str(info.value) or "may not" in str(info.value)
    assert not adapter.lua_managed_path.exists()
