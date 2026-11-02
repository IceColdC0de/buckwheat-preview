"""Session tests: FakeProvider, FakeRunner and tmp_path only."""

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from buckwheat.adapters.base import (
    CommandResult,
    FakeRunner,
    RenderResult,
    load_managed_state,
)
from buckwheat.adapters.hyprland import HyprlandAdapter
from buckwheat.adapters.waybar import WaybarAdapter
from buckwheat.config import SafetySettings, Settings
from buckwheat.plan import ChangePlan
from buckwheat.providers.base import BadOutputError, NetworkError
from buckwheat.providers.fake import FakeProvider
from buckwheat.safety import snapshot as snaps
from buckwheat.session import (
    PlanRejectedError,
    Session,
    StaleProposalError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hyprland"
NOW = datetime(2030, 1, 1, tzinfo=UTC)


def plan(*ops: dict[str, object], summary: str = "test") -> ChangePlan:
    return ChangePlan.model_validate({"version": 1, "summary": summary, "ops": ops})


def gaps(value: int = 6) -> dict[str, object]:
    return {
        "op": "set",
        "target": "hyprland",
        "key": "general.gaps_in",
        "value": value,
    }


class Env:
    def __init__(self, tmp_path: Path, runner: FakeRunner) -> None:
        self.home = tmp_path / "home"
        self.conf = self.home / ".config" / "hypr"
        shutil.copytree(FIXTURES / "bootstrapped", self.conf)
        self.runner = runner
        self.adapter = HyprlandAdapter(runner, config_dir=self.conf)
        # Instance-level allowlist: the class default is fixed at import time.
        self.adapter.managed_files = [self.adapter.managed_path]  # type: ignore[misc]
        self.state_path = self.home / ".local" / "state" / "buckwheat" / "managed.json"

    def session(
        self, provider: FakeProvider, retries: int = 1, timeout: int = 15
    ) -> Session:
        settings = Settings(
            safety=SafetySettings(max_retries=retries, revert_timeout_seconds=timeout)
        )
        return Session(
            provider,
            settings,
            {"hyprland": self.adapter},
            home=self.home,
            state_path=self.state_path,
            clock=lambda: NOW,
        )

    def tree(self) -> dict[str, bytes]:
        return {
            str(p): p.read_bytes()
            for p in sorted(self.home.rglob("*"))
            if p.is_file() and "snapshots" not in p.parts
        }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    yield Env(tmp_path, FakeRunner())


def test_happy_path_apply_keep(env: Env) -> None:
    provider = FakeProvider(default=plan(gaps(6)))
    s = env.session(provider)
    proposal = s.propose("more gaps")
    assert "gaps_in = 6" in proposal.diff
    assert "+++ b/~/.config/hypr/buckwheat.conf" in proposal.diff
    assert proposal.warnings == ()
    change = s.apply(proposal)
    assert change.state == "pending"
    assert change.revert_deadline == NOW + timedelta(seconds=15)
    assert "gaps_in = 6" in env.adapter.managed_path.read_text()
    assert ("hyprctl", "reload") in [c[0] for c in env.runner.calls]
    s.keep(change.id)
    assert snaps.history()[0].state == "kept"
    # The value is remembered, so a second proposal keeps it.
    s2 = env.session(FakeProvider(default=plan({**gaps(), "key": "general.gaps_out"})))
    p2 = s2.propose("x")
    assert "gaps_in" in p2.files[env.adapter.managed_path]


def nord() -> dict[str, object]:
    return {"op": "palette", "palette": "nord", "targets": ["hyprland"]}


def test_palette_colours_survive_a_later_set_op(env: Env) -> None:
    s = env.session(FakeProvider(default=plan(nord())))
    s.keep(s.apply(s.propose("nord")).id)
    saved = load_managed_state(env.state_path)["hyprland"]
    assert saved["general.col.active_border"] == "#88c0d0"

    s2 = env.session(FakeProvider(default=plan(gaps(6))))
    proposal = s2.propose("more gaps")
    assert proposal.state_updates == {"hyprland": {"general.gaps_in": 6}}
    s2.keep(s2.apply(proposal).id)
    text = env.adapter.managed_path.read_text()
    assert "col.active_border = rgba(88c0d0ff)" in text
    assert "col.inactive_border = rgba(434c5eff)" in text
    assert "gaps_in = 6" in text
    state = load_managed_state(env.state_path)["hyprland"]
    assert state["general.col.active_border"] == "#88c0d0"
    assert state["general.gaps_in"] == 6


def test_context_lists_keys_that_are_not_set_yet(env: Env) -> None:
    provider = FakeProvider(default=plan(gaps()))
    env.session(provider).propose("hi")
    keys = provider.calls[0][1].limits.keys["hyprland"]
    assert keys == env.adapter.allowed_keys()
    assert "general.col.active_border" in keys  # nothing saved yet


def test_context_is_built_without_dotfile_text(env: Env) -> None:
    provider = FakeProvider(default=plan(gaps()))
    env.session(provider).propose("hi")
    ctx = provider.calls[0][1]
    names = {t.name: t for t in ctx.targets}
    assert names["hyprland"].detected and not names["kitty"].detected
    assert ctx.palettes and "hyprland" in ctx.limits.keys


def test_propose_has_no_side_effects(env: Env) -> None:
    before = env.tree()
    s = env.session(FakeProvider(default=plan(gaps())))
    s.propose("x")
    assert env.tree() == before
    assert not env.state_path.exists()
    assert snaps.history() == []
    assert [c[0] for c in env.runner.calls if c[0][1] == "reload"] == []


def test_hostile_plan_rejected_no_writes(env: Env) -> None:
    hostile = plan(
        {"op": "set", "target": "hyprland", "key": "exec-once", "value": "x"}
    )
    before = env.tree()
    provider = FakeProvider(default=hostile)
    s = env.session(provider)
    with pytest.raises(PlanRejectedError) as ei:
        s.propose("pwn")
    assert ei.value.problems[0].op_index == 0
    assert env.tree() == before
    assert snaps.history() == []
    assert len(provider.calls) == 2  # original + one retry


def test_undetected_target_rejected(env: Env) -> None:
    kitty = plan({"op": "palette", "palette": "nord", "targets": ["kitty"]})
    with pytest.raises(PlanRejectedError) as ei:
        env.session(FakeProvider(default=kitty), retries=0).propose("x")
    assert ei.value.problems[0].code == "no-adapter"


def test_conflicting_ops_rejected(env: Env) -> None:
    bad = plan(gaps(4), gaps(8))
    with pytest.raises(PlanRejectedError) as ei:
        env.session(FakeProvider(default=bad), retries=0).propose("x")
    assert ei.value.problems[0].code == "conflict"


class Scripted:
    """Provider returning scripted results in order; records requests."""

    name = "scripted"

    def __init__(self, *results: ChangePlan | Exception) -> None:
        self.results = list(results)
        self.requests: list[str] = []

    def plan(self, request: str, context: object) -> ChangePlan:
        self.requests.append(request)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_retry_once_with_error_text_then_success(env: Env) -> None:
    bad = plan({"op": "set", "target": "hyprland", "key": "nope", "value": 1})
    provider = Scripted(bad, plan(gaps()))
    p = env.session(provider).propose("make it nice")  # type: ignore[arg-type]
    assert p.attempts == 2
    assert provider.requests[0] == "make it nice"
    assert "unknown_key" in provider.requests[1]
    assert provider.requests[1].startswith("make it nice")


def test_retry_once_then_error(env: Env) -> None:
    provider = Scripted(BadOutputError("garbled"), BadOutputError("still garbled"))
    with pytest.raises(PlanRejectedError, match="still garbled"):
        env.session(provider).propose("x")  # type: ignore[arg-type]
    assert len(provider.requests) == 2


def test_network_error_not_retried(env: Env) -> None:
    provider = FakeProvider(error=NetworkError("down"))
    with pytest.raises(NetworkError):
        env.session(provider).propose("x")
    assert len(provider.calls) == 1


def test_revert_restores_bytes(env: Env) -> None:
    s = env.session(FakeProvider(default=plan(gaps())))
    before = env.tree()
    change = s.apply(s.propose("x"))
    assert env.tree() != before
    result = s.revert(change.id)
    assert result.ok
    assert env.tree() == before  # new files removed, old bytes back
    assert snaps.history()[0].state == "reverted"
    assert [c[0] for c in env.runner.calls].count(("hyprctl", "reload")) == 2


def test_failed_reload_auto_reverts(env: Env) -> None:
    env.runner.results[("hyprctl", "configerrors")] = CommandResult(0, "bad value")
    s = env.session(FakeProvider(default=plan(gaps())))
    before = env.tree()
    change = s.apply(s.propose("x"))
    assert change.state == "reverted"
    assert "bad value" in change.messages[0]
    assert env.tree() == before
    assert snaps.history()[0].state == "reverted"


def test_waybar_failed_reload_then_revert_spawns_waybar_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    waybar_dir = home / ".config" / "waybar"
    waybar_dir.mkdir(parents=True)
    golden = Path(__file__).parent / "fixtures" / "waybar" / "golden"
    (waybar_dir / "config.jsonc").write_text(
        (golden / "config.jsonc.installed").read_text()
    )
    (waybar_dir / "style.css").write_text((golden / "style.css.installed").read_text())

    uid = str(os.getuid())
    pgrep = ("pgrep", "-U", uid, "-x", "waybar")
    pkill = ("pkill", "-USR2", "-U", uid, "-x", "waybar")

    class Dying(FakeRunner):
        def run(self, argv, timeout=10.0):  # type: ignore[no-untyped-def]
            res = super().run(argv, timeout)
            if tuple(argv) == pkill:
                self.results[pgrep] = CommandResult(1)
            return res

    runner = Dying(
        {
            ("waybar", "--version"): CommandResult(0, "waybar v0.15.0\n"),
            pgrep: CommandResult(0),
            pkill: CommandResult(0),
        }
    )
    adapter = WaybarAdapter(waybar_dir, runner, sleep=lambda _s: None)
    adapter.managed_files = list(adapter.files)

    provider = FakeProvider(
        default=plan(
            {"op": "set", "target": "waybar", "key": "bar.height", "value": 30}
        )
    )
    settings = Settings(
        safety=SafetySettings(max_retries=1, revert_timeout_seconds=15)
    )
    state_path = home / ".local" / "state" / "buckwheat" / "managed.json"
    s = Session(
        provider,
        settings,
        {"waybar": adapter},
        home=home,
        state_path=state_path,
        clock=lambda: NOW,
    )
    change = s.apply(s.propose("change waybar height"))
    assert change.state == "reverted"
    assert "waybar exited after reload" in change.messages[0]
    assert runner.spawned == [("waybar",)]


def test_stale_proposal_refused(env: Env) -> None:
    s = env.session(FakeProvider(default=plan(gaps())))
    p = s.propose("x")
    env.adapter.managed_path.write_text("user edit\n")
    with pytest.raises(StaleProposalError):
        s.apply(p)


def test_recover_reverts_only_stale_pending(env: Env) -> None:
    s = env.session(FakeProvider(default=plan(gaps())))
    before = env.tree()
    s.apply(s.propose("x"))
    assert s.recover() == []  # deadline not reached: maybe a live session
    assert env.tree() != before
    later = Session(
        FakeProvider(),
        Settings(),
        {"hyprland": env.adapter},
        state_path=env.state_path,
        clock=lambda: NOW + timedelta(minutes=5),
    )
    assert len(later.recover()) == 1
    assert env.tree() == before


def _render_deletes(adapter: HyprlandAdapter, *paths: Path) -> None:
    """Make the adapter's render ask for deletions (what uninstall will do)."""
    managed = adapter.managed_path
    if not managed.exists():
        managed.write_bytes(b"# Managed by buckwheat.\r\ngeneral {\n}\n")

    def render(ops: object, state: object) -> RenderResult:
        return RenderResult(deletes=frozenset(paths))

    adapter.render = render  # type: ignore[method-assign,assignment]


def test_apply_deletes_managed_file_and_revert_recreates_it(env: Env) -> None:
    managed = env.adapter.managed_path
    _render_deletes(env.adapter, managed)
    original = managed.read_bytes()
    s = env.session(FakeProvider(default=plan(gaps(6))))
    proposal = s.propose("remove")
    assert proposal.deletes == {managed.resolve()}
    assert proposal.files == {}
    assert "--- a/~/.config/hypr/buckwheat.conf\n+++ /dev/null\n" in proposal.diff
    assert "-" + original.decode().splitlines()[0] in proposal.diff
    change = s.apply(proposal)
    assert change.state == "pending"
    assert not managed.exists()
    s.revert(change.id)
    assert managed.read_bytes() == original


def test_failed_reload_after_delete_restores_file(env: Env) -> None:
    managed = env.adapter.managed_path
    _render_deletes(env.adapter, managed)
    original = managed.read_bytes()
    env.runner.default = CommandResult(1, stderr="boom")
    s = env.session(FakeProvider(default=plan(gaps(6))))
    change = s.apply(s.propose("remove"))
    assert change.state == "reverted"
    assert managed.read_bytes() == original


def test_delete_of_absent_file_is_a_quiet_noop(env: Env) -> None:
    managed = env.adapter.managed_path
    _render_deletes(env.adapter, managed)
    managed.unlink()
    s = env.session(FakeProvider(default=plan(gaps(6))))
    proposal = s.propose("remove")
    assert "buckwheat.conf" not in proposal.diff
    assert s.apply(proposal).state == "pending"
    assert not managed.exists()


def test_delete_of_users_main_config_is_rejected(env: Env) -> None:
    main = env.adapter.main_path
    before = main.read_bytes()
    _render_deletes(env.adapter, main)
    s = env.session(FakeProvider(default=plan(gaps(6))))
    with pytest.raises(PlanRejectedError) as err:
        s.propose("remove")
    assert "path-not-allowed" in [p.code for p in err.value.problems]
    assert main.read_bytes() == before
