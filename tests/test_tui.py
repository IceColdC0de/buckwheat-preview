"""TUI tests with Textual's Pilot: FakeProvider, FakeRunner and tmp_path only."""

import asyncio
import shutil
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from textual.pilot import Pilot
from textual.widgets import DataTable, Input, Static

from buckwheat.adapters.base import FakeRunner
from buckwheat.adapters.hyprland import HyprlandAdapter
from buckwheat.config import SafetySettings, Settings
from buckwheat.plan import ChangePlan
from buckwheat.providers.base import NetworkError
from buckwheat.providers.fake import FakeProvider
from buckwheat.safety import snapshot as snaps
from buckwheat.session import Session
from buckwheat.tui.app import BuckwheatApp
from buckwheat.tui.screens import (
    ConfirmScreen,
    CountdownScreen,
    ErrorScreen,
    HistoryScreen,
    PreviewScreen,
    RequestScreen,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hyprland"


def plan(*ops: dict[str, object]) -> ChangePlan:
    return ChangePlan.model_validate(
        {"version": 1, "summary": "Wider gaps", "ops": ops}
    )


def gaps(value: int = 6) -> dict[str, object]:
    return {"op": "set", "target": "hyprland", "key": "general.gaps_in", "value": value}


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.conf = self.home / ".config" / "hypr"
        shutil.copytree(FIXTURES / "bootstrapped", self.conf)
        self.runner = FakeRunner()
        self.adapter = HyprlandAdapter(self.runner, config_dir=self.conf)
        self.adapter.managed_files = [self.adapter.managed_path]  # type: ignore[misc]
        self.state_path = self.home / ".local" / "state" / "buckwheat" / "managed.json"

    def app(self, provider: FakeProvider, timeout: int = 15) -> BuckwheatApp:
        settings = Settings(
            safety=SafetySettings(max_retries=0, revert_timeout_seconds=timeout)
        )
        session = Session(
            provider,
            settings,
            {"hyprland": self.adapter},
            home=self.home,
            state_path=self.state_path,
        )
        return BuckwheatApp(session)

    @property
    def managed(self) -> str:
        path = self.adapter.managed_path
        return path.read_text() if path.exists() else ""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    yield Env(tmp_path)


async def wait_for(pilot: Pilot[None], cond: Callable[[], bool]) -> None:
    for _ in range(100):
        await pilot.pause(0.05)
        if cond():
            return
    raise AssertionError("timed out waiting for the UI")


def on_screen(app: BuckwheatApp, cls: type[Any]) -> Callable[[], bool]:
    return lambda: isinstance(app.screen, cls)


def drive(app: BuckwheatApp, script: Callable[[Pilot[None]], Awaitable[None]]) -> None:
    async def main() -> None:
        async with app.run_test() as pilot:
            await wait_for(pilot, on_screen(app, RequestScreen))
            await script(pilot)

    asyncio.run(main())


async def ask(app: BuckwheatApp, pilot: Pilot[None], text: str) -> None:
    await pilot.click(Input)
    await pilot.press(*text, "enter")


def static_text(app: BuckwheatApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).content)


def test_request_preview_confirm_keep(env: Env) -> None:
    app = env.app(FakeProvider(default=plan(gaps(6))))

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "more gaps")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        assert "Wider gaps" in static_text(app, "#plan-summary")
        assert "gaps_in = 6" not in env.managed  # nothing written yet
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        assert "gaps_in = 6" in env.managed
        await pilot.press("k")
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert snaps.history()[0].state == "kept"
        assert "gaps_in = 6" in env.managed

    drive(app, script)


def test_preview_shows_diff_and_plain_summary(env: Env) -> None:
    app = env.app(FakeProvider(default=plan(gaps(6))))

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "more gaps")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        assert "+++ b/~/.config/hypr/buckwheat.conf" in static_text(app, "#diff-body")
        lines = [str(w.content) for w in app.screen.query(".op-line").results(Static)]
        assert lines == ["* Set general.gaps_in to 6 in hyprland"]

    drive(app, script)


def test_cancel_writes_nothing(env: Env) -> None:
    app = env.app(FakeProvider(default=plan(gaps(6))))
    before = env.managed

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "more gaps")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        await pilot.press("escape")
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert env.managed == before
        assert snaps.history() == []
        assert ("hyprctl", "reload") not in [c[0] for c in env.runner.calls]

    drive(app, script)


def test_timeout_reverts(env: Env) -> None:
    app = env.app(FakeProvider(default=plan(gaps(6))), timeout=1)
    before = env.managed

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "more gaps")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        assert "gaps_in = 6" in env.managed
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert env.managed == before
        assert snaps.history()[0].state == "reverted"

    drive(app, script)


def test_escape_reverts_during_countdown(env: Env) -> None:
    app = env.app(FakeProvider(default=plan(gaps(6))))
    before = env.managed

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "more gaps")
        await wait_for(pilot, on_screen(app, PreviewScreen))
        await pilot.press("enter")
        await wait_for(pilot, on_screen(app, CountdownScreen))
        await pilot.press("escape")
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert env.managed == before
        assert snaps.history()[0].state == "reverted"

    drive(app, script)


def test_rejected_plan_shows_reason(env: Env) -> None:
    bad = {"op": "set", "target": "hyprland", "key": "exec-once", "value": "x"}
    app = env.app(FakeProvider(default=plan(bad)))
    before = env.managed

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "run something")
        await wait_for(pilot, on_screen(app, ErrorScreen))
        reasons = " ".join(
            str(w.content) for w in app.screen.query(".error-reason").results(Static)
        )
        assert "Op 1" in reasons
        assert "exec-once" in reasons or "key" in reasons
        await pilot.press("escape")
        await wait_for(pilot, on_screen(app, RequestScreen))
        assert env.managed == before
        assert snaps.history() == []

    drive(app, script)


def test_provider_error_is_shown(env: Env) -> None:
    app = env.app(FakeProvider(error=NetworkError("offline")))

    async def script(pilot: Pilot[None]) -> None:
        await ask(app, pilot, "anything")
        await wait_for(pilot, on_screen(app, ErrorScreen))
        reasons = " ".join(
            str(w.content) for w in app.screen.query(".error-reason").results(Static)
        )
        assert "offline" in reasons

    drive(app, script)


def kept_change(env: Env) -> None:
    """Apply and keep a change through Session, outside the UI."""
    session = env.app(FakeProvider(default=plan(gaps(6)))).session
    session.keep(session.apply(session.propose("more gaps")).id)


def open_history(app: BuckwheatApp, pilot: Pilot[None]) -> Awaitable[None]:
    async def go() -> None:
        await pilot.press("f2")
        await wait_for(pilot, on_screen(app, HistoryScreen))

    return go()


def test_history_rollback(env: Env) -> None:
    before = env.managed
    kept_change(env)
    assert "gaps_in = 6" in env.managed
    app = env.app(FakeProvider())

    async def script(pilot: Pilot[None]) -> None:
        await open_history(app, pilot)
        assert app.screen.query_one(DataTable).row_count == 1
        await pilot.press("r")
        await wait_for(pilot, lambda: snaps.history()[0].state == "reverted")
        assert env.managed == before

    drive(app, script)


def test_history_warns_when_files_changed_since(env: Env) -> None:
    kept_change(env)
    edited = env.managed + "# my edit\n"
    env.adapter.managed_path.write_text(edited)
    app = env.app(FakeProvider())

    async def script(pilot: Pilot[None]) -> None:
        await open_history(app, pilot)
        await pilot.press("r")
        await wait_for(pilot, on_screen(app, ConfirmScreen))
        assert env.managed == edited
        await pilot.press("escape")  # No
        await wait_for(pilot, on_screen(app, HistoryScreen))
        assert env.managed == edited
        assert snaps.history()[0].state == "kept"

        await pilot.press("r")
        await wait_for(pilot, on_screen(app, ConfirmScreen))
        await pilot.press("enter")  # Yes
        await wait_for(pilot, lambda: snaps.history()[0].state == "reverted")
        assert "my edit" not in env.managed

    drive(app, script)


def test_history_only_rolls_back_kept_changes(env: Env) -> None:
    session = env.app(FakeProvider(default=plan(gaps(6)))).session
    session.revert(session.apply(session.propose("x")).id)
    app = env.app(FakeProvider())
    after = env.managed

    async def script(pilot: Pilot[None]) -> None:
        await open_history(app, pilot)
        await pilot.press("r")
        await pilot.pause(0.3)
        assert isinstance(app.screen, HistoryScreen)
        assert env.managed == after

    drive(app, script)
