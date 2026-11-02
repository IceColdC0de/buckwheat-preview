"""A change to targets that are not set up yet sets them up in the same step.

Owner's first desktop test: kitty and hyprland (Lua) not bootstrapped, a Nord
request, Enter, keep, and nothing changed on screen because no program read
buckwheat's files. Now the preview is "Set up and apply": one diff with the
include blocks and the change, one snapshot, one keep-or-revert countdown.
"""

import os
import shutil
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button

from buckwheat.adapters.alacritty import AlacrittyAdapter
from buckwheat.adapters.base import (
    INCLUDE_BEGIN,
    Adapter,
    CommandError,
    CommandResult,
    FakeRunner,
)
from buckwheat.adapters.hyprland import HyprlandAdapter
from buckwheat.adapters.kitty import KittyAdapter
from buckwheat.config import SafetySettings, Settings
from buckwheat.plan import ChangePlan
from buckwheat.providers.fake import FakeProvider
from buckwheat.safety import snapshot as snaps
from buckwheat.session import Session, SessionError
from buckwheat.tui.app import BuckwheatApp
from buckwheat.tui.screens import (
    CountdownScreen,
    ErrorScreen,
    PreviewScreen,
    RequestScreen,
)
from test_tui import ask, drive, on_screen, static_text, wait_for
from test_tui_bootstrap import op_warnings

FIXTURES = Path(__file__).parent / "fixtures"
HYPR_NOT_RUNNING = {("hyprctl", "version"): CommandError("not running")}
ALACRITTY_014 = {("alacritty", "--version"): CommandResult(0, "alacritty 0.14.0\n")}


def nord(*targets: str) -> ChangePlan:
    return ChangePlan.model_validate(
        {
            "version": 1,
            "summary": "Nord everywhere",
            "ops": [{"op": "palette", "palette": "nord", "targets": list(targets)}],
        }
    )


class Desktop:
    """Fresh HOME: kitty (stock kitty.conf) and hyprland.lua, neither set up."""

    def __init__(self, home: Path, *, bootstrap: str = "confirm") -> None:
        self.home = home
        self.state = home / ".local" / "state" / "buckwheat" / "managed.json"
        self.bootstrap = bootstrap
        kitty_dir = home / ".config" / "kitty"
        kitty_dir.mkdir(parents=True)
        self.kitty_conf = kitty_dir / "kitty.conf"
        self.kitty_conf.write_bytes((FIXTURES / "kitty" / "kitty.conf").read_bytes())
        self.kitty = KittyAdapter(FakeRunner(), kitty_dir, self.state)
        self.kitty.managed_files = [self.kitty.managed_file]  # type: ignore[misc]
        hypr_dir = home / ".config" / "hypr"
        shutil.copytree(FIXTURES / "hyprland" / "lua_only", hypr_dir)
        self.hypr = HyprlandAdapter(FakeRunner(HYPR_NOT_RUNNING), config_dir=hypr_dir)
        self.hypr.managed_files = [  # type: ignore[misc]
            self.hypr.managed_path,
            self.hypr.lua_managed_path,
        ]
        self.adapters: dict[str, Adapter] = {
            "hyprland": self.hypr,
            "kitty": self.kitty,
        }

    def add_refused_alacritty(self) -> None:
        """alacritty with a multi-line import array: its setup is refused."""
        d = self.home / ".config" / "alacritty"
        d.mkdir(parents=True)
        conf = d / "alacritty.toml"
        fixture = FIXTURES / "alacritty" / "multiline_import.toml"
        conf.write_bytes(fixture.read_bytes())
        a = AlacrittyAdapter(
            FakeRunner(ALACRITTY_014),
            user_conf=conf,
            managed_conf=d / "buckwheat.toml",
            state_path=self.state,
            home=self.home,
            which=lambda name: f"/usr/bin/{name}",
        )
        a.managed_files = [a.managed_conf]  # type: ignore[misc]
        self.adapters["alacritty"] = a

    def session(self, plan: ChangePlan) -> Session:
        return Session(
            FakeProvider(default=plan),
            Settings(
                bootstrap=self.bootstrap,  # type: ignore[arg-type]
                safety=SafetySettings(max_retries=0, revert_timeout_seconds=30),
            ),
            self.adapters,
            home=self.home,
            state_path=self.state,
        )

    def tree(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.home)): p.read_bytes()
            for p in sorted(self.home.rglob("*"))
            if p.is_file()
        }

    def is_set_up(self) -> bool:
        return (
            INCLUDE_BEGIN in self.kitty_conf.read_text()
            and INCLUDE_BEGIN in self.hypr.lua_path.read_text()
        )

    def has_nord(self) -> bool:
        # Nord's polar-night background, in each program's own syntax.
        kitty = self.kitty.managed_file.read_text().lower()
        hypr = self.hypr.lua_managed_path.read_text().lower()
        return "2e3440" in kitty and "88c0d0" in hypr


@pytest.fixture
def desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Desktop:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    if os.geteuid() == 0:  # atomic_write refuses root; the sandbox may be root
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
    return Desktop(home)


# --- Session ---------------------------------------------------------------


def test_proposal_sets_up_and_changes_in_one_snapshot(desk: Desktop) -> None:
    s = desk.session(nord("hyprland", "kitty"))
    before = desk.tree()
    p = s.propose("nord please")
    assert p.setup == ("hyprland", "kitty")
    assert p.skipped == () and p.warnings == ()
    assert INCLUDE_BEGIN in p.setup_diff
    assert "kitty.conf" in p.setup_diff and "hyprland.lua" in p.setup_diff
    assert sorted(p.setup_files) == [
        "~/.config/hypr/hyprland.lua",
        "~/.config/kitty/kitty.conf",
    ]
    assert desk.tree() == before  # propose writes nothing

    change = s.apply(p)
    assert change.state == "pending" and change.not_showing == ()
    s.keep(change.id)
    assert desk.is_set_up() and desk.has_nord()
    assert len(s.history()) == 1  # one entry for setup + change
    assert s.needs_install(["hyprland", "kitty"]) == []


def test_revert_undoes_setup_and_change_together(desk: Desktop) -> None:
    s = desk.session(nord("hyprland", "kitty"))
    before = desk.tree()
    change = s.apply(s.propose("nord please"))
    assert desk.is_set_up()
    assert s.revert(change.id, force=True).ok
    after = desk.tree()
    assert {k: v for k, v in after.items() if "snapshots" not in k} == before


def test_bootstrapped_targets_get_a_plain_apply(desk: Desktop) -> None:
    s = desk.session(nord("kitty"))
    s.keep(s.apply_bootstrap(s.propose_install(["kitty"])).id)
    p = s.propose("nord please")
    assert p.setup == () and p.setup_diff == "" and not p.allow


def test_refused_setup_is_skipped_and_the_rest_apply(desk: Desktop) -> None:
    desk.add_refused_alacritty()
    alacritty_before = desk.tree()[".config/alacritty/alacritty.toml"]
    s = desk.session(nord("alacritty", "hyprland", "kitty"))
    p = s.propose("nord please")
    assert p.setup == ("hyprland", "kitty")
    assert "alacritty" not in p.adapters
    assert len(p.skipped) == 1 and p.skipped[0].startswith("alacritty: ")
    assert "alacritty" not in p.state_updates
    s.keep(s.apply(p).id)
    assert desk.is_set_up() and desk.has_nord()
    tree = desk.tree()
    assert tree[".config/alacritty/alacritty.toml"] == alacritty_before
    assert ".config/alacritty/buckwheat.toml" not in tree


def test_manual_mode_shows_instructions_and_skips_unset_targets(
    desk: Desktop,
) -> None:
    desk.bootstrap = "manual"
    s = desk.session(nord("hyprland", "kitty"))
    before = desk.tree()
    p = s.propose("nord please")
    assert p.setup == () and p.setup_diff == ""
    assert p.empty and p.adapters == ()
    assert INCLUDE_BEGIN in p.instructions
    assert [r.split(":")[0] for r in p.skipped] == ["hyprland", "kitty"]
    assert all("manual" in r for r in p.skipped)
    with pytest.raises(SessionError, match="nothing to write"):
        s.apply(p)
    assert desk.tree() == before


def test_manual_mode_applies_to_targets_the_user_set_up(desk: Desktop) -> None:
    # kitty was set up by hand; hyprland was not.
    s = desk.session(nord("hyprland", "kitty"))
    s.keep(s.apply_bootstrap(s.propose_install(["kitty"])).id)
    desk.bootstrap = "manual"
    s = desk.session(nord("hyprland", "kitty"))
    p = s.propose("nord please")
    assert p.adapters == ("kitty",) and p.setup == ()
    assert [r.split(":")[0] for r in p.skipped] == ["hyprland"]
    s.keep(s.apply(p).id)
    assert "2e3440" in desk.kitty.managed_file.read_text().lower()
    assert not desk.hypr.lua_managed_path.exists()


def test_apply_reports_targets_that_still_do_not_include_us(desk: Desktop) -> None:
    s = desk.session(nord("hyprland", "kitty"))
    p = s.propose("nord please")
    # Simulate an adapter whose include check disagrees after the write.
    real = desk.kitty.read_state

    def stale_state():  # type: ignore[no-untyped-def]
        return real().__class__(values=real().values, bootstrap="none")

    desk.kitty.read_state = stale_state  # type: ignore[method-assign]
    change = s.apply(p)
    assert change.not_showing == ("kitty",)


# --- TUI (Pilot) -------------------------------------------------------------


def test_tui_set_up_and_apply_then_keep(desk: Desktop) -> None:
    app = BuckwheatApp(desk.session(nord("hyprland", "kitty")))
    before = desk.tree()

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "nord")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        notice = static_text(app, "#setup-notice")
        assert notice.startswith("Set up and apply: hyprland, kitty")
        assert "include block" in notice and "kitty.conf" in notice
        assert INCLUDE_BEGIN in static_text(app, "#setup-diff")
        assert static_text(app, "#diff-body")  # the change itself
        labels = [str(b.label) for b in app.screen.query(Button)]
        assert labels[0] == "Set up and apply (Enter)"
        assert not app.screen.query("#install")  # the old 2-step offer is gone
        assert desk.tree() == before
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        assert desk.is_set_up() and desk.has_nord()
        await pilot.press("k")
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert desk.is_set_up() and desk.has_nord()
        history = snaps.history()
        assert len(history) == 1 and history[0].state == "kept"

    drive(app, script)


def test_tui_escape_during_countdown_restores_every_file(desk: Desktop) -> None:
    app = BuckwheatApp(desk.session(nord("hyprland", "kitty")))
    config = desk.home / ".config"
    before = {p: p.read_bytes() for p in config.rglob("*") if p.is_file()}

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "nord")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        assert desk.is_set_up()
        await pilot.press("escape")
        await wait_for(pilot, on_screen(app, RequestScreen))
        after = {p: p.read_bytes() for p in config.rglob("*") if p.is_file()}
        assert after == before
        history = snaps.history()
        assert len(history) == 1 and history[0].state == "reverted"

    drive(app, script)


def test_tui_refused_target_is_listed_and_the_rest_apply(desk: Desktop) -> None:
    desk.add_refused_alacritty()
    app = BuckwheatApp(desk.session(nord("alacritty", "hyprland", "kitty")))

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "nord")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        warnings = op_warnings(app)
        assert any(w.startswith("! skipped alacritty: ") for w in warnings), warnings
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        await pilot.press("k")
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert desk.is_set_up() and desk.has_nord()
        assert not (desk.home / ".config" / "alacritty" / "buckwheat.toml").exists()

    drive(app, script)


def test_tui_manual_mode_shows_instructions_and_cannot_apply(desk: Desktop) -> None:
    desk.bootstrap = "manual"
    app = BuckwheatApp(desk.session(nord("hyprland", "kitty")))
    before = desk.tree()

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "nord")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        assert INCLUDE_BEGIN in static_text(app, "#setup-instructions")
        assert not app.screen.query("#setup-notice")
        assert not app.screen.query("#confirm")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert isinstance(app.screen, PreviewScreen)
        assert desk.tree() == before
        assert snaps.history() == []

    drive(app, script)


def test_tui_keep_says_which_programs_will_not_show_it(desk: Desktop) -> None:
    app = BuckwheatApp(desk.session(nord("hyprland", "kitty")))
    real = desk.kitty.read_state

    def stale_state():  # type: ignore[no-untyped-def]
        # Reports "not set up" even after the include was written.
        return real().__class__(values=real().values, bootstrap="none")

    desk.kitty.read_state = stale_state  # type: ignore[method-assign]

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "nord")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        assert "kitty won't show this change" in static_text(app, "#not-showing")
        await pilot.press("k")
        await wait_for(pilot, on_screen(app, ErrorScreen))
        assert "Kept, but" in static_text(app, "#error-head")

    drive(app, script)
