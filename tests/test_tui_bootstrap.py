"""TUI install (bootstrap) and uninstall flows (M13 part 2).

Textual Pilot with FakeProvider, FakeRunner and tmp_path only. The hyprland
config here is the un-bootstrapped ``pristine`` fixture.
"""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Static

from buckwheat.adapters.base import INCLUDE_BEGIN, FakeRunner
from buckwheat.adapters.hyprland import HyprlandAdapter
from buckwheat.adapters.kitty import KittyAdapter
from buckwheat.config import SafetySettings, Settings
from buckwheat.providers.fake import FakeProvider
from buckwheat.safety import snapshot as snaps
from buckwheat.session import Session
from buckwheat.tui.app import BuckwheatApp
from buckwheat.tui.screens import (
    BootstrapScreen,
    CountdownScreen,
    LeftBehindScreen,
    PreviewScreen,
    RequestScreen,
    UninstallScreen,
)
from test_tui import FIXTURES, ask, drive, gaps, on_screen, plan, static_text, wait_for

KITTY_CONF = Path(__file__).parent / "fixtures" / "kitty" / "kitty.conf"
BARE_INCLUDE = b"include buckwheat.conf\n"


class Env:
    def __init__(self, tmp_path: Path, bootstrap: str = "confirm") -> None:
        self.home = tmp_path / "home"
        self.conf = self.home / ".config" / "hypr"
        shutil.copytree(FIXTURES / "pristine", self.conf)
        self.original = tree(self.conf)
        self.runner = FakeRunner()
        self.adapter = HyprlandAdapter(self.runner, config_dir=self.conf)
        self.adapter.managed_files = [self.adapter.managed_path]  # type: ignore[misc]
        self.state_path = self.home / ".local" / "state" / "buckwheat" / "managed.json"
        self.bootstrap = bootstrap

    def session(self, provider: FakeProvider) -> Session:
        settings = Settings(
            bootstrap=self.bootstrap,  # type: ignore[arg-type]
            safety=SafetySettings(max_retries=0, revert_timeout_seconds=30),
        )
        return Session(
            provider,
            settings,
            {"hyprland": self.adapter},
            home=self.home,
            state_path=self.state_path,
        )

    def app(self, provider: FakeProvider) -> BuckwheatApp:
        return BuckwheatApp(self.session(provider))

    @property
    def main_conf(self) -> str:
        return (self.conf / "hyprland.conf").read_text()


def tree(d: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(d)): p.read_bytes()
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh HOME and XDG dirs under tmp_path, so the real ones are never used."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    if os.geteuid() == 0:  # atomic_write refuses root; the sandbox may be root
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
    return home


@pytest.fixture
def env(tmp_path: Path, isolated_home: Path) -> Iterator[Env]:
    yield Env(tmp_path)


class KittyEnv:
    """kitty only, with the stock kitty.conf. Its include line is the bootstrap edit."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.conf_dir = home / ".config" / "kitty"
        self.conf_dir.mkdir(parents=True)
        self.user_conf = self.conf_dir / "kitty.conf"
        self.user_conf.write_bytes(KITTY_CONF.read_bytes())
        self.state_path = home / ".local" / "state" / "buckwheat" / "managed.json"
        self.adapter = KittyAdapter(FakeRunner(), self.conf_dir, self.state_path)
        self.adapter.managed_files = [self.adapter.managed_file]  # type: ignore[misc]

    def session(self, provider: FakeProvider) -> Session:
        settings = Settings(
            safety=SafetySettings(max_retries=0, revert_timeout_seconds=30)
        )
        return Session(
            provider,
            settings,
            {"kitty": self.adapter},
            home=self.home,
            state_path=self.state_path,
        )

    def app(self, provider: FakeProvider) -> BuckwheatApp:
        return BuckwheatApp(self.session(provider))

    def install(self) -> None:
        s = self.session(FakeProvider())
        s.keep(s.apply_bootstrap(s.propose_install(["kitty"])).id)


@pytest.fixture
def kitty_env(isolated_home: Path) -> KittyEnv:
    return KittyEnv(isolated_home)


def op_warnings(app: BuckwheatApp) -> list[str]:
    """Text of every warning line (the red "! ..." rows) on the current screen."""
    return [
        str(w.content) for w in app.screen.query(Static) if "op-warning" in w.classes
    ]


def test_preview_sets_up_and_applies_in_one_step(env: Env) -> None:
    app = env.app(FakeProvider(default=plan(gaps(6))))

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "more gaps")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        assert "hyprland" in static_text(app, "#setup-notice")
        assert INCLUDE_BEGIN in static_text(app, "#setup-diff")
        assert not app.screen.query("#install")  # no separate setup step
        assert env.main_conf == env.original["hyprland.conf"].decode()  # not yet
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        assert INCLUDE_BEGIN in env.main_conf
        await pilot.press("k")
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert INCLUDE_BEGIN in env.main_conf
        assert "gaps_in" in env.adapter.managed_path.read_text()
        assert [i.state for i in snaps.history()] == ["kept"]
        assert app.session.needs_install(["hyprland"]) == []

    drive(app, script)


def test_no_setup_shown_when_bootstrapped(env: Env) -> None:
    session = env.session(FakeProvider())
    session.keep(session.apply_bootstrap(session.propose_install(["hyprland"])).id)
    app = env.app(FakeProvider(default=plan(gaps(6))))

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "more gaps")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        assert not app.screen.query("#setup-notice")
        assert not app.screen.query("#setup-diff")
        assert str(app.screen.query_one("#confirm", Button).label) == "Apply (Enter)"

    drive(app, script)


def test_cancel_set_up_and_apply_writes_nothing(env: Env) -> None:
    app = env.app(FakeProvider(default=plan(gaps(6))))

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "more gaps")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        await pilot.press("escape")
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert tree(env.conf) == env.original
        assert snaps.history() == []
        assert ("hyprctl", "reload") not in [c[0] for c in env.runner.calls]

    drive(app, script)


def test_set_up_and_apply_revert_restores_files(env: Env) -> None:
    app = env.app(FakeProvider(default=plan(gaps(6))))

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "more gaps")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        await pilot.press("r")
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert tree(env.conf) == env.original
        assert [i.state for i in snaps.history()] == ["reverted"]

    drive(app, script)


@pytest.mark.parametrize("row", [0, 1], ids=["all", "hyprland"])
def test_uninstall_restores_files_byte_for_byte(env: Env, row: int) -> None:
    # Install and make one change through Session, so every managed file exists.
    setup = env.session(FakeProvider(default=plan(gaps(6))))
    setup.keep(setup.apply_bootstrap(setup.propose_install(["hyprland"])).id)
    setup.keep(setup.apply(setup.propose("more gaps")).id)
    assert tree(env.conf) != env.original
    app = env.app(FakeProvider())

    async def script(pilot: Pilot[None]) -> None:
        await pilot.press("f3")
        await wait_for(pilot, on_screen(app, UninstallScreen))
        await pilot.press("down", *(["down"] * row), "enter")
        await wait_for(pilot, on_screen(app, BootstrapScreen))
        assert "Uninstall" in static_text(app, "#bootstrap-title")
        assert INCLUDE_BEGIN in static_text(app, "#diff-body")
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        await pilot.press("k")
        await wait_for(pilot, on_screen(app, UninstallScreen))
        assert tree(env.conf) == env.original

    drive(app, script)


def test_cancel_uninstall_writes_nothing(env: Env) -> None:
    setup = env.session(FakeProvider())
    setup.keep(setup.apply_bootstrap(setup.propose_install(["hyprland"])).id)
    installed = tree(env.conf)
    app = env.app(FakeProvider())

    async def script(pilot: Pilot[None]) -> None:
        await pilot.press("f3")
        await wait_for(pilot, on_screen(app, UninstallScreen))
        await pilot.press("down", "enter")
        await wait_for(pilot, on_screen(app, BootstrapScreen))
        await pilot.press("escape")
        await wait_for(pilot, on_screen(app, UninstallScreen))
        assert tree(env.conf) == installed

    drive(app, script)


def test_manual_uninstall_shows_instructions_and_writes_nothing(env: Env) -> None:
    setup = env.session(FakeProvider())
    setup.keep(setup.apply_bootstrap(setup.propose_install(["hyprland"])).id)
    installed = tree(env.conf)
    env.bootstrap = "manual"
    app = env.app(FakeProvider())

    async def script(pilot: Pilot[None]) -> None:
        await pilot.press("f3")
        await wait_for(pilot, on_screen(app, UninstallScreen))
        await pilot.press("down", "enter")
        await wait_for(pilot, on_screen(app, BootstrapScreen))
        assert INCLUDE_BEGIN in static_text(app, "#bootstrap-instructions")
        assert not app.screen.query("#confirm")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert tree(env.conf) == installed

    drive(app, script)


def test_uninstall_preview_lists_a_bare_include_left_in_kitty(
    kitty_env: KittyEnv,
) -> None:
    # kitty.conf keeps buckwheat's marked block plus a bare include line outside it.
    kitty_env.install()
    kitty_env.user_conf.write_bytes(kitty_env.user_conf.read_bytes() + BARE_INCLUDE)
    app = kitty_env.app(FakeProvider())

    async def script(pilot: Pilot[None]) -> None:
        await pilot.press("f3")
        await wait_for(pilot, on_screen(app, UninstallScreen))
        await pilot.press("down", "enter")  # row 1 is kitty
        await wait_for(pilot, on_screen(app, BootstrapScreen))
        warnings = op_warnings(app)
        assert any(
            w.startswith("! remove by hand: kitty: ") and "include buckwheat.conf" in w
            for w in warnings
        ), warnings
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        await pilot.press("k")
        await wait_for(pilot, on_screen(app, LeftBehindScreen))
        head = static_text(app, "#left-behind-head")
        assert head.startswith("Removed what buckwheat could remove. ")
        assert "A backup is kept as snapshot" in head
        shown = [str(s.content) for s in app.screen.query(Static)]
        assert any("include buckwheat.conf" in text for text in shown)
        assert kitty_env.user_conf.read_bytes().endswith(BARE_INCLUDE)  # not edited
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, UninstallScreen))

    drive(app, script)


def test_uninstall_preview_shows_no_leftovers_when_none_are_left(
    kitty_env: KittyEnv,
) -> None:
    kitty_env.install()
    app = kitty_env.app(FakeProvider())

    async def script(pilot: Pilot[None]) -> None:
        await pilot.press("f3")
        await wait_for(pilot, on_screen(app, UninstallScreen))
        await pilot.press("down", "enter")
        await wait_for(pilot, on_screen(app, BootstrapScreen))
        assert INCLUDE_BEGIN in static_text(app, "#diff-body")  # something to remove
        assert not [w for w in op_warnings(app) if "remove by hand" in w]
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        await pilot.press("k")
        await wait_for(pilot, on_screen(app, UninstallScreen))
        await pilot.pause(0.3)  # a result screen would be pushed after this
        assert isinstance(app.screen, UninstallScreen)
        assert kitty_env.user_conf.read_bytes() == KITTY_CONF.read_bytes()

    drive(app, script)
