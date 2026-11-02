"""Hyprland 0.57+ Lua config (hyprland.lua).

Fixtures under tests/fixtures/hyprland/:
- ``lua_only``: one hyprland.lua, no hyprland.conf;
- ``lua_split``: hyprland.lua that requires modules; ``mytheme/look.lua`` sets
  ``general.col.active_border`` before buckwheat's block;
- ``pristine``: the classic hyprland.conf (Hyprland < 0.57);
- the symlinked hyprland.lua (dotfiles) is built in the test.

Tests marked ``needs_hyprland`` run the real ``Hyprland --verify-config`` and
are skipped when the binary is not installed (GitHub CI does not have it).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from buckwheat import check
from buckwheat.adapters.base import (
    DEFAULT_TIMEOUT,
    MANAGED_HEADER,
    CommandError,
    CommandResult,
    FakeRunner,
    SubprocessRunner,
    save_managed_state,
)
from buckwheat.adapters.hyprland import (
    CLASSIC_KIND,
    LUA_BEGIN,
    LUA_END,
    LUA_INCLUDE_LINE,
    LUA_KIND,
    LUA_MANAGED_HEADER,
    NO_LUA_CONFIG,
    HyprlandAdapter,
)
from buckwheat.adapters.wallpaper import WallpaperAdapter
from buckwheat.config import SafetySettings, Settings
from buckwheat.plan import ChangePlan
from buckwheat.plan.models import PaletteOp, SetOp
from buckwheat.providers.fake import FakeProvider
from buckwheat.safety.bootstrap import check_include_edit
from buckwheat.session import PlanRejectedError, Session

FIXTURES = Path(__file__).parent / "fixtures" / "hyprland"
VERSION = ("hyprctl", "version")
NOT_RUNNING = {VERSION: CommandError("not running")}
HYPRLAND = shutil.which("Hyprland")
needs_hyprland = pytest.mark.skipif(
    HYPRLAND is None,
    reason="Hyprland binary not installed: real --verify-config tests need 0.57+",
)
BLOCK = f"{LUA_BEGIN}\n{LUA_INCLUDE_LINE}\n{LUA_END}\n"


class RealRunner:
    """Runs the real checker; answers ``hyprctl`` (no compositor here) from a fake."""

    def __init__(self, hyprctl: FakeRunner) -> None:
        self.hyprctl = hyprctl
        self.real = SubprocessRunner()
        self.calls: list[tuple[str, ...]] = []

    def run(
        self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT
    ) -> CommandResult:
        self.calls.append(tuple(argv))
        if argv[0] == "hyprctl":
            return self.hyprctl.run(argv, timeout)
        return self.real.run(argv, timeout)

    def spawn(self, argv: Sequence[str]) -> None:
        self.calls.append(tuple(argv))
        if argv[0] == "hyprctl":
            self.hyprctl.spawn(argv)
            return
        self.real.spawn(argv)


class Capture(FakeRunner):
    """Records the argv and the checked folder of each --verify-config call."""

    def __init__(self, result: CommandResult | CommandError) -> None:
        super().__init__(NOT_RUNNING)
        self.result = result
        self.seen: list[tuple[list[str], dict[str, bytes]]] = []

    def run(
        self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT
    ) -> CommandResult:
        if "--verify-config" not in argv:
            return super().run(argv, timeout)
        root = Path(argv[-1]).parent
        files = {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }
        self.seen.append((list(argv), files))
        if isinstance(self.result, CommandError):
            raise self.result
        return self.result


class Env:
    def __init__(
        self,
        tmp_path: Path,
        scenario: str | None,
        runner: FakeRunner | RealRunner | None = None,
        hyprland_bin: str | None = None,
    ) -> None:
        self.home = tmp_path / "home"
        self.conf = self.home / ".config" / "hypr"
        if scenario is not None:
            shutil.copytree(FIXTURES / scenario, self.conf)
        else:
            self.conf.mkdir(parents=True)
        self.runner = runner if runner is not None else FakeRunner(NOT_RUNNING)
        self.adapter = HyprlandAdapter(
            self.runner, config_dir=self.conf, hyprland_bin=hyprland_bin
        )
        self.adapter.managed_files = [  # type: ignore[misc]
            self.adapter.managed_path,
            self.adapter.lua_managed_path,
        ]
        self.state_path = self.home / ".local" / "state" / "buckwheat" / "managed.json"

    def session(self, provider: FakeProvider | None = None) -> Session:
        return Session(
            provider or FakeProvider(),
            Settings(safety=SafetySettings(max_retries=0)),
            {"hyprland": self.adapter},
            home=self.home,
            state_path=self.state_path,
        )

    def tree(self, root: Path | None = None) -> dict[str, bytes]:
        root = root or self.home
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file() and not p.is_symlink()
        }

    def install(self) -> None:
        s = self.session()
        proposal = s.propose_install(["hyprland"])
        assert proposal.skipped == ()
        s.keep(s.apply_bootstrap(proposal).id)

    def set_values(self, plan: ChangePlan) -> None:
        s = self.session(FakeProvider(default=plan))
        s.keep(s.apply(s.propose("x")).id)

    def uninstall(self) -> tuple[str, ...]:
        s = self.session()
        proposal = s.propose_uninstall(["hyprland"])
        s.keep(s.apply_bootstrap(proposal).id)
        return proposal.left_behind


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    if os.geteuid() == 0:  # atomic_write refuses root; the sandbox may be root
        monkeypatch.setattr(os, "geteuid", lambda: 1000)


@pytest.fixture
def lua(tmp_path: Path) -> Env:
    return Env(tmp_path, "lua_only")


def plan_of(*ops: dict[str, object]) -> ChangePlan:
    return ChangePlan.model_validate({"version": 1, "summary": "t", "ops": list(ops)})


def set_(key: str, value: object) -> dict[str, object]:
    return {"op": "set", "target": "hyprland", "key": key, "value": value}


ALL_KEYS: dict[str, object] = {
    "animations.enabled": False,
    "decoration.blur.size": 4,
    "decoration.blur.enabled": True,
    "decoration.inactive_opacity": 1.0,
    "decoration.active_opacity": 0.9,
    "decoration.rounding": 8,
    "general.col.inactive_border": "#3b4252",
    "general.col.active_border": "#88C0D0",
    "general.border_size": 2,
    "general.gaps_out": 12,
    "general.gaps_in": 6,
}


def all_ops() -> list[SetOp]:
    return [
        SetOp(op="set", target="hyprland", key=k, value=v)  # type: ignore[arg-type]
        for k, v in ALL_KEYS.items()
    ]


def real_verify(config: Path, *, home: Path | None = None) -> str:
    """Run the real checker; ``home`` set means Hyprland's normal lookup (no -c)."""
    assert HYPRLAND is not None
    runtime = config.parent / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    env = [f"XDG_RUNTIME_DIR={runtime}"]
    args = [HYPRLAND, "--verify-config"]
    if home is not None:
        env += [f"HOME={home}", f"XDG_CONFIG_HOME={home / '.config'}"]
    else:
        args += ["-c", str(config)]
    res = SubprocessRunner().run(["env", *env, *args], timeout=30)
    return (res.stdout + res.stderr).split("Config parsing result:")[-1].strip()


# --- detection -------------------------------------------------------------


def test_lua_file_means_lua_config(lua: Env) -> None:
    det = lua.adapter.detect()
    assert det.installed and det.config_kind == LUA_KIND
    assert lua.adapter.uses_lua()
    assert lua.adapter.unsupported_reason() is None
    plan = lua.adapter.bootstrap_plan()
    assert (plan.file, plan.line) == (lua.adapter.lua_path, LUA_INCLUDE_LINE)
    assert lua.adapter.read_state().bootstrap == "none"


@pytest.mark.parametrize("out", ["Hyprland 0.57.0\n", "Hyprland 0.60.1", "v1.0.0"])
def test_0_57_without_hyprland_lua_refuses_install(tmp_path: Path, out: str) -> None:
    """Even with an old hyprland.conf around: 0.57 does not read it."""
    env = Env(tmp_path, "pristine", FakeRunner({VERSION: CommandResult(0, out)}))
    before = env.tree()
    assert env.adapter.uses_lua()
    assert env.adapter.unsupported_reason() == NO_LUA_CONFIG
    with pytest.raises(ValueError, match="does not create your main config"):
        env.adapter.install(env.adapter.bootstrap_plan())
    proposal = env.session().propose_install(["hyprland"])
    assert proposal.skipped == ("hyprland: " + NO_LUA_CONFIG,) and proposal.empty
    assert env.tree() == before
    assert not env.adapter.lua_path.exists()


def test_classic_config_below_0_57_still_works(tmp_path: Path) -> None:
    """(d) hyprlang path: buckwheat.conf plus a `source =` block in hyprland.conf."""
    runner = FakeRunner({VERSION: CommandResult(0, "Hyprland 0.56.9\n")})
    env = Env(tmp_path, "pristine", runner)
    original = env.adapter.main_path.read_bytes()
    a = env.adapter
    assert not a.uses_lua() and a.detect().config_kind == CLASSIC_KIND
    assert a.bootstrap_plan().file == a.main_path
    assert a.bootstrap_plan().line.startswith("source = ")
    env.install()
    assert a.read_state().bootstrap == "bootstrapped"
    env.set_values(plan_of(set_("general.gaps_in", 4)))
    assert "gaps_in = 4" in a.managed_path.read_text()
    assert not a.lua_managed_path.exists() and not a.lua_path.exists()
    a.reload()
    assert ("hyprctl", "reload") in [c[0] for c in runner.calls]
    env.uninstall()
    assert a.main_path.read_bytes() == original
    assert not a.managed_path.exists()


# --- rendering -------------------------------------------------------------


def test_render_lua_golden_in_registry_order(lua: Env) -> None:
    golden = (FIXTURES / "all_keys.lua.golden").read_text()
    state = lua.adapter.read_state()
    result = lua.adapter.render(all_ops(), state)  # type: ignore[arg-type]
    assert dict(result.files) == {lua.adapter.lua_managed_path: golden}
    again = lua.adapter.render(list(reversed(all_ops())), state)  # type: ignore[arg-type]
    assert again.files[lua.adapter.lua_managed_path] == golden
    assert golden.startswith(LUA_MANAGED_HEADER + "\n")
    assert golden.count("hl.config(") == 1


def test_render_lua_palette_uses_rgb_strings(lua: Env) -> None:
    op = PaletteOp(op="palette", palette="nord", targets=["hyprland"])
    text = lua.adapter.render([op], lua.adapter.read_state()).files[
        lua.adapter.lua_managed_path
    ]
    assert 'active_border = "rgb(88c0d0)",' in text
    assert 'inactive_border = "rgb(' in text


def test_font_ops_and_unknown_keys_are_still_rejected(lua: Env) -> None:
    bad = SetOp(op="set", target="hyprland", key="general.gaps_inn", value=1)
    assert [p.code for p in lua.adapter.validate(bad)] == ["unknown_key"]
    other = SetOp(op="set", target="kitty", key="font_size", value=12)
    assert lua.adapter.validate(other) == []


# --- install / uninstall (adapter level) ----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "hl.config({ general = { gaps_in = 6 } })\n",
        "hl.config({ general = { gaps_in = 6 } })",  # no final newline
        "-- crlf\r\nhl.config({ general = { gaps_in = 6 } })\r\n",
        "",
    ],
)
def test_install_appends_block_and_uninstall_undoes_it(lua: Env, text: str) -> None:
    a = lua.adapter
    a.lua_path.write_bytes(text.encode())
    result = a.install(a.bootstrap_plan())
    new = result.files[a.lua_path]
    assert new.startswith(text)
    assert new.rstrip("\r\n").endswith(LUA_END)
    assert LUA_INCLUDE_LINE in new
    check_include_edit(text, new)  # the session's marker-only rule accepts it
    assert result.files[a.lua_managed_path].startswith(LUA_MANAGED_HEADER)
    a.lua_path.write_bytes(new.encode())
    a.lua_managed_path.write_text(result.files[a.lua_managed_path])
    assert a.read_state().bootstrap == "bootstrapped"
    with pytest.raises(ValueError, match="already has a buckwheat block"):
        a.install(a.bootstrap_plan())
    undone = a.uninstall()
    assert undone.files == {a.lua_path: text}
    assert undone.deletes == frozenset({a.lua_managed_path})
    assert undone.leftovers == ()


def test_uninstall_keeps_a_user_owned_buckwheat_lua(lua: Env) -> None:
    lua.adapter.lua_managed_path.write_text("-- mine\n")
    assert lua.adapter.uninstall().deletes == frozenset()


def test_uninstall_reports_stray_requires_outside_the_block(lua: Env) -> None:
    a = lua.adapter
    text = a.lua_path.read_text()
    stray = (
        text
        + 'require("buckwheat")\n'
        + "  pcall(require, 'buckwheat')\n"
        + '-- require("buckwheat") in a comment is fine\n'
        + 'require("buckwheat_extra")\n'
        + BLOCK
    )
    a.lua_path.write_text(stray)
    result = a.uninstall()
    assert result.files[a.lua_path] == text + 'require("buckwheat")\n' + (
        "  pcall(require, 'buckwheat')\n"
        '-- require("buckwheat") in a comment is fine\n'
        'require("buckwheat_extra")\n'
    )
    assert len(result.leftovers) == 2
    assert "`require(\"buckwheat\")`" in result.leftovers[0]
    assert "`pcall(require, 'buckwheat')`" in result.leftovers[1]
    assert "remove that line by hand" in result.leftovers[0]


def test_uninstall_also_cleans_the_old_classic_files(lua: Env) -> None:
    """Upgraded from < 0.57: the old buckwheat.conf and source block go too."""
    a = lua.adapter
    old = "monitor=,preferred,auto,1\n"
    block = (
        "# >>> buckwheat (managed include) >>>\n"
        "source = ~/.config/hypr/buckwheat.conf\n"
        "# <<< buckwheat <<<\n"
    )
    a.main_path.write_text(old + block)
    a.managed_path.write_text(MANAGED_HEADER + "\n")
    result = a.uninstall()
    assert result.files == {a.main_path: old}
    assert result.deletes == frozenset({a.managed_path})


# --- reload ----------------------------------------------------------------


def test_reload_is_config_only_then_configerrors(lua: Env) -> None:
    assert lua.adapter.reload().ok
    calls = [c[0] for c in lua.runner.calls if c[0] != VERSION]  # type: ignore[union-attr]
    assert calls == [("hyprctl", "reload", "config-only"), ("hyprctl", "configerrors")]


def test_reload_fails_on_config_errors(tmp_path: Path) -> None:
    runner = FakeRunner(
        {**NOT_RUNNING, ("hyprctl", "configerrors"): CommandResult(0, "bad key")}
    )
    env = Env(tmp_path, "lua_only", runner)
    result = env.adapter.reload()
    assert not result.ok and "bad key" in result.message


# --- the pre-write check (fake checker: runs everywhere) ------------------


def test_verify_checks_a_copy_with_the_proposed_files(tmp_path: Path) -> None:
    runner = Capture(CommandResult(0, "config ok\n"))
    env = Env(tmp_path, "lua_split", runner, hyprland_bin="/opt/Hyprland")
    (env.conf / "wallpaper.png").write_bytes(b"\0" * (5 * 1024 * 1024))
    before = env.tree()
    result = env.adapter.render(all_ops(), env.adapter.read_state())  # type: ignore[arg-type]
    assert env.tree() == before  # nothing written next to the real config
    [(argv, files)] = runner.seen
    assert argv[0] == "env" and argv[1].startswith("XDG_RUNTIME_DIR=")
    assert argv[2:5] == ["/opt/Hyprland", "--verify-config", "-c"]
    assert argv[5].endswith("/hyprland.lua")
    assert not argv[5].startswith(str(env.conf))
    assert files["buckwheat.lua"] == result.files[env.adapter.lua_managed_path].encode()
    # Not installed yet: the copy gets the block so buckwheat.lua is checked.
    original = (env.conf / "hyprland.lua").read_bytes()
    assert files["hyprland.lua"] == original + BLOCK.encode()
    assert "mytheme/look.lua" in files and "mytheme/binds.lua" in files
    assert "wallpaper.png" not in files  # big files are left out


def test_verify_error_refuses_render_with_the_real_path(tmp_path: Path) -> None:
    out = (
        "noise\n======== Config parsing result:\n\n"
        "{root}/buckwheat.lua:3: unknown config key 'general.gaps_inn'\n"
    )

    class Fails(Capture):
        def run(
            self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT
        ) -> CommandResult:
            if "--verify-config" in argv:
                self.result = CommandResult(1, out.format(root=Path(argv[-1]).parent))
            return super().run(argv, timeout)

    env = Env(tmp_path, "lua_only", Fails(CommandResult(1)), hyprland_bin="Hyprland")
    with pytest.raises(ValueError) as ei:
        env.adapter.render(all_ops(), env.adapter.read_state())  # type: ignore[arg-type]
    msg = str(ei.value)
    assert msg.startswith("Hyprland --verify-config: ")
    assert f"{env.conf}/buckwheat.lua:3: unknown config key" in msg
    assert "noise" not in msg
    # Through the session the plan is refused and nothing is written.
    before = env.tree()
    with pytest.raises(PlanRejectedError) as pe:
        env.session(FakeProvider(default=plan_of(set_("general.gaps_in", 4)))).propose(
            "x"
        )
    assert pe.value.problems[0].code == "render-failed"
    assert "unknown config key" in pe.value.problems[0].message
    assert env.tree() == before


def test_verify_error_refuses_install(tmp_path: Path) -> None:
    runner = Capture(CommandResult(1, "Config parsing result:\nx.lua:1: boom\n"))
    env = Env(tmp_path, "lua_only", runner, hyprland_bin="Hyprland")
    before = env.tree()
    proposal = env.session().propose_install(["hyprland"])
    assert proposal.empty
    assert proposal.skipped == ("hyprland: Hyprland --verify-config: x.lua:1: boom",)
    assert env.tree() == before


@pytest.mark.parametrize(
    "result", [CommandError("gone"), CommandResult(127, "", "env: not found")]
)
def test_missing_checker_falls_back_to_own_checks(
    tmp_path: Path, result: CommandResult | CommandError
) -> None:
    env = Env(tmp_path, "lua_only", Capture(result), hyprland_bin="Hyprland")
    out = env.adapter.render(all_ops(), env.adapter.read_state())  # type: ignore[arg-type]
    assert env.adapter.lua_managed_path in out.files


def test_no_binary_means_no_checker(lua: Env) -> None:
    assert lua.adapter.hyprland_bin is None
    lua.adapter.render(all_ops(), lua.adapter.read_state())  # type: ignore[arg-type]
    assert all("--verify-config" not in c[0] for c in lua.runner.calls)  # type: ignore[union-attr]


# --- through the session ---------------------------------------------------


def test_split_modules_block_goes_after_them(tmp_path: Path) -> None:
    """(b) mytheme/look.lua sets the border colour first; buckwheat's block is last."""
    env = Env(tmp_path, "lua_split")
    look = (env.conf / "mytheme" / "look.lua").read_bytes()
    original = env.adapter.lua_path.read_text()
    env.install()
    text = env.adapter.lua_path.read_text()
    assert text == original + BLOCK
    assert text.index('require("mytheme.look")') < text.index(LUA_INCLUDE_LINE)
    env.set_values(plan_of(set_("general.col.active_border", "#88c0d0")))
    managed = env.adapter.lua_managed_path.read_text()
    assert 'active_border = "rgb(88c0d0)"' in managed
    assert (env.conf / "mytheme" / "look.lua").read_bytes() == look
    assert env.adapter.lua_path.read_text() == original + BLOCK


def test_symlinked_hyprland_lua_is_edited_at_its_target(tmp_path: Path) -> None:
    """(c) dotfiles: the link stays a link; its target gets the block."""
    env = Env(tmp_path, None)
    dot = env.home / "dotfiles" / "hypr"
    dot.mkdir(parents=True)
    target = dot / "hyprland.lua"
    shutil.copy(FIXTURES / "lua_only" / "hyprland.lua", target)
    original = target.read_bytes()
    env.adapter.lua_path.symlink_to(target)
    link = os.readlink(env.adapter.lua_path)

    env.install()
    assert env.adapter.lua_path.is_symlink()
    assert os.readlink(env.adapter.lua_path) == link
    assert target.read_bytes() == original + BLOCK.encode()
    managed = env.adapter.lua_managed_path
    assert managed.is_file() and not managed.is_symlink()
    assert not (dot / "buckwheat.lua").exists()

    # A reverted change restores the target and keeps the link.
    s = env.session(FakeProvider(default=plan_of(set_("general.gaps_in", 9))))
    change = s.apply(s.propose("x"))
    assert "gaps_in = 9" in managed.read_text()
    s.revert(change.id)
    assert "gaps_in = 9" not in managed.read_text()
    assert env.adapter.lua_path.is_symlink()

    env.uninstall()
    assert env.adapter.lua_path.is_symlink()
    assert os.readlink(env.adapter.lua_path) == link
    assert target.read_bytes() == original
    assert not managed.exists()


def test_dangling_symlink_is_skipped_with_a_clear_message(tmp_path: Path) -> None:
    env = Env(tmp_path, None)
    env.adapter.lua_path.symlink_to(env.home / "dotfiles" / "gone.lua")
    assert env.adapter.uses_lua()
    proposal = env.session().propose_install(["hyprland"])
    assert proposal.empty
    assert proposal.skipped == (
        "hyprland: hyprland.lua is a symlink to a file that does not exist",
    )


@pytest.mark.parametrize("scenario", ["lua_only", "lua_split"])
def test_install_render_uninstall_restores_byte_for_byte(
    tmp_path: Path, scenario: str
) -> None:
    """(e) the config folder ends up exactly as it started."""
    env = Env(tmp_path, scenario)
    before = env.tree(env.conf)
    env.install()
    env.set_values(plan_of(*(set_(k, v) for k, v in ALL_KEYS.items())))
    assert env.adapter.lua_managed_path.read_text() == (
        FIXTURES / "all_keys.lua.golden"
    ).read_text()
    assert env.uninstall() == ()
    assert env.tree(env.conf) == before


def test_wallpaper_does_not_need_hyprland_conf(lua: Env) -> None:
    """hyprpaper.conf is its own file; the wallpaper adapter never reads hyprland.*."""
    wall = WallpaperAdapter(
        FakeRunner(), conf_path=lua.conf / "hyprpaper.conf", home=lua.home
    )
    wall.managed_files = [wall.conf_path]  # type: ignore[misc]
    s = Session(
        FakeProvider(),
        Settings(),
        {"wallpaper": wall},
        home=lua.home,
        state_path=lua.state_path,
    )
    assert wall.bootstrap_plan() is None and wall.bootstrap_files() == []
    proposal = s.propose_install(["wallpaper"])
    assert set(proposal.files) <= {wall.conf_path}
    assert not (lua.conf / "hyprland.conf").exists()


# --- buckwheat check -------------------------------------------------------


def run_check(
    env: Env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    version: CommandResult | CommandError,
) -> tuple[int, str, str]:
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x" * 20)
    if not env.state_path.exists():
        save_managed_state({}, env.state_path)
    runner = FakeRunner(
        {
            VERSION: version,
            ("kitty", "--version"): CommandError("no kitty"),
            ("waybar", "--version"): CommandError("no waybar"),
        }
    )
    code = check.run_check(runner=runner)
    out = capsys.readouterr().out
    hypr = out.split("\nhyprland\n")[1].split("\n\n")[0]
    return code, hypr, out.split("result:")[1]


def test_check_reports_the_lua_config_and_include(
    lua: Env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, hypr, _ = run_check(lua, tmp_path, monkeypatch, capsys, CommandError("no"))
    assert f"config: {LUA_KIND}" in hypr
    assert f"user file: {lua.conf / 'hyprland.lua'} (exists)" in hypr
    assert f"managed file: {lua.conf / 'buckwheat.lua'} (missing)" in hypr
    assert "hyprland.conf" not in hypr and "not supported" not in hypr
    assert "bootstrapped: no (status: none)" in hypr

    lua.install()
    _, hypr, _ = run_check(lua, tmp_path, monkeypatch, capsys, CommandError("no"))
    assert "bootstrapped: yes (status: bootstrapped)" in hypr
    assert f"managed file: {lua.conf / 'buckwheat.lua'} (exists)" in hypr


def test_check_reports_0_57_without_hyprland_lua(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = Env(tmp_path, None)
    code, hypr, result = run_check(
        env, tmp_path, monkeypatch, capsys, CommandResult(0, "Hyprland 0.57.0")
    )
    assert f"config: {LUA_KIND}" in hypr
    assert f"not supported: {NO_LUA_CONFIG}" in hypr
    assert f"hyprland: {NO_LUA_CONFIG}" in result
    assert code == 1


def test_check_reports_the_classic_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = Env(tmp_path, "pristine")
    _, hypr, _ = run_check(env, tmp_path, monkeypatch, capsys, CommandError("no"))
    assert f"config: {CLASSIC_KIND}" in hypr
    assert f"user file: {env.conf / 'hyprland.conf'} (exists)" in hypr
    assert f"managed file: {env.conf / 'buckwheat.conf'}" in hypr


# --- the real Hyprland --verify-config (skipped without the binary) -------


def real_env(tmp_path: Path, scenario: str | None) -> Env:
    return Env(tmp_path, scenario, RealRunner(FakeRunner(NOT_RUNNING)), HYPRLAND)


@needs_hyprland
@pytest.mark.parametrize("scenario", ["lua_only", "lua_split"])
def test_real_checker_accepts_the_rendered_config(
    tmp_path: Path, scenario: str
) -> None:
    env = real_env(tmp_path, scenario)
    env.install()
    env.set_values(plan_of(*(set_(k, v) for k, v in ALL_KEYS.items())))
    runner = env.runner
    assert isinstance(runner, RealRunner)
    assert sum("--verify-config" in c for c in runner.calls) == 2  # install, render
    assert real_verify(env.adapter.lua_path) == "config ok"
    # The include really loads buckwheat.lua: a bad key in it is reported.
    managed = env.adapter.lua_managed_path
    managed.write_text(managed.read_text().replace("gaps_in", "gaps_inn"))
    assert "unknown config key 'general.gaps_inn'" in real_verify(env.adapter.lua_path)


@needs_hyprland
def test_real_checker_symlinked_config_normal_lookup(tmp_path: Path) -> None:
    """Without -c, Hyprland loads modules from ~/.config/hypr, where the link is."""
    env = real_env(tmp_path, None)
    dot = env.home / "dotfiles" / "hypr"
    dot.mkdir(parents=True)
    shutil.copy(FIXTURES / "lua_only" / "hyprland.lua", dot / "hyprland.lua")
    env.adapter.lua_path.symlink_to(dot / "hyprland.lua")
    env.install()
    env.set_values(plan_of(set_("general.gaps_in", 9)))
    assert real_verify(env.adapter.lua_path, home=env.home) == "config ok"
    managed = env.adapter.lua_managed_path
    managed.write_text(managed.read_text().replace("gaps_in", "gaps_inn"))
    assert "gaps_inn" in real_verify(env.adapter.lua_path, home=env.home)


@needs_hyprland
def test_real_checker_refuses_a_bad_buckwheat_lua(tmp_path: Path) -> None:
    env = real_env(tmp_path, "lua_only")
    bad = "hl.config({ general = { gaps_inn = 1 } })\n"
    with pytest.raises(ValueError) as ei:
        env.adapter._verify({env.adapter.lua_managed_path: bad})
    assert f"{env.conf}/buckwheat.lua:1: unknown config key 'general.gaps_inn'" in str(
        ei.value
    )


@needs_hyprland
def test_real_checker_reports_an_error_in_the_users_module(tmp_path: Path) -> None:
    env = real_env(tmp_path, "lua_split")
    look = env.conf / "mytheme" / "look.lua"
    look.write_text(look.read_text().replace("gaps_in", "gaps_inx"))
    before = env.tree()
    with pytest.raises(PlanRejectedError) as ei:
        env.session(FakeProvider(default=plan_of(set_("general.gaps_in", 4)))).propose(
            "x"
        )
    message = ei.value.problems[0].message
    assert f"{look}:" in message and "general.gaps_inx" in message
    assert env.tree() == before
