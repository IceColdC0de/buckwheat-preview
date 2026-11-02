import os
from pathlib import Path

import pytest

from buckwheat.adapters import base
from buckwheat.adapters.base import (
    INCLUDE_BEGIN,
    CommandError,
    CommandResult,
    FakeRunner,
    RenderResult,
    TargetState,
    save_managed_state,
)
from buckwheat.adapters.kitty import KittyAdapter
from buckwheat.plan.models import FontOp, Op, PaletteOp, SetOp
from buckwheat.safety import atomic_write, safe_delete, unified_diff

FIXTURES = Path(__file__).parent / "fixtures" / "kitty"
UID = str(os.getuid())


@pytest.fixture
def env(tmp_path: Path) -> tuple[KittyAdapter, FakeRunner, Path]:
    cfg = tmp_path / "kitty"
    cfg.mkdir()
    (cfg / "kitty.conf").write_text((FIXTURES / "kitty.conf").read_text())
    runner = FakeRunner()
    adapter = KittyAdapter(runner, cfg, tmp_path / "state" / "managed.json")
    return adapter, runner, cfg


def _write(adapter: KittyAdapter, result: RenderResult) -> None:
    for path, text in result.files.items():
        atomic_write(path, text, [adapter.managed_file, adapter.user_file])
    for path in result.deletes:
        safe_delete(path, [adapter.managed_file])  # never the user's file


PLAN_OPS: list[Op] = [
    PaletteOp(op="palette", palette="nord", variant=None, targets=["kitty"]),
    SetOp(op="set", target="kitty", key="background_opacity", value=0.9),
    FontOp(op="font", target="kitty", role="terminal", family="Fira Code", size=12),
]


def test_registered() -> None:
    assert base.get("kitty") is KittyAdapter  # type: ignore[comparison-overlap]


def test_render_matches_golden(env: tuple[KittyAdapter, FakeRunner, Path]) -> None:
    adapter, _, cfg = env
    state = adapter.read_state()
    result = adapter.render(PLAN_OPS, state)
    text = result.files[adapter.managed_file]
    golden = FIXTURES / "golden_buckwheat.conf"
    if not golden.exists():  # pragma: no cover
        golden.write_text(text)
    assert text == golden.read_text()
    diff = unified_diff("", text, "buckwheat.conf")
    golden_diff = FIXTURES / "golden_buckwheat.diff"
    if not golden_diff.exists():  # pragma: no cover
        golden_diff.write_text(diff)
    assert diff == golden_diff.read_text()


def test_apply_is_idempotent_and_leaves_user_config(
    env: tuple[KittyAdapter, FakeRunner, Path],
) -> None:
    adapter, _, cfg = env
    user_before = (cfg / "kitty.conf").read_text()
    _write(adapter, adapter.install(adapter.bootstrap_plan()))
    installed = (cfg / "kitty.conf").read_text()
    assert installed.startswith(user_before)
    assert INCLUDE_BEGIN in installed and "include buckwheat.conf" in installed

    first = adapter.render(PLAN_OPS, adapter.read_state())
    _write(adapter, first)
    # Session persists the merged values; re-applying must change nothing.
    save_managed_state(
        {"kitty": {"background_opacity": 0.9, "font_family": "Fira Code"}},
        tmp := cfg.parent / "state" / "managed.json",
    )
    assert tmp.exists()
    second = adapter.render(PLAN_OPS, adapter.read_state())
    assert second == adapter.render(PLAN_OPS, adapter.read_state())
    assert second.files[adapter.managed_file] == first.files[adapter.managed_file]
    assert (cfg / "kitty.conf").read_text() == installed
    # Installing again is a no-op.
    again = adapter.install(adapter.bootstrap_plan())
    assert again.files[adapter.user_file] == installed


def test_state_overlay_and_ordering(
    env: tuple[KittyAdapter, FakeRunner, Path],
) -> None:
    adapter, _, _ = env
    state = TargetState(values={"font_size": 11.0, "cursor_shape": "beam"})
    op = SetOp(op="set", target="kitty", key="font_size", value=14.0)
    text = adapter.render([op], state).files[adapter.managed_file]
    assert "font_size 14.0\n" in text
    assert "cursor_shape beam\n" in text
    assert text.index("font_size") < text.index("cursor_shape")


def test_uninstall_restores_user_file(
    env: tuple[KittyAdapter, FakeRunner, Path],
) -> None:
    adapter, _, cfg = env
    original = (cfg / "kitty.conf").read_bytes()
    before = sorted(p.name for p in cfg.iterdir())
    _write(adapter, adapter.install(adapter.bootstrap_plan()))
    assert adapter.managed_file.exists()
    result = adapter.uninstall()
    assert result.files == {adapter.user_file: original.decode()}
    assert result.deletes == {adapter.managed_file}
    _write(adapter, result)
    assert (cfg / "kitty.conf").read_bytes() == original
    assert sorted(p.name for p in cfg.iterdir()) == before


def test_uninstall_keeps_user_owned_managed_path(
    env: tuple[KittyAdapter, FakeRunner, Path],
) -> None:
    adapter, _, _ = env
    adapter.managed_file.write_text("font_size 12\n")  # the user's own file
    assert adapter.uninstall().deletes == frozenset()


def test_read_state_bootstrap_statuses(
    env: tuple[KittyAdapter, FakeRunner, Path],
) -> None:
    adapter, _, cfg = env
    assert adapter.read_state().bootstrap == "none"
    _write(adapter, adapter.install(adapter.bootstrap_plan()))
    assert adapter.read_state().bootstrap == "bootstrapped"
    with (cfg / "kitty.conf").open("a") as f:
        f.write("font_size 20\nmap a b\n")
    state = adapter.read_state()
    assert state.bootstrap == "user-shadowed"
    assert state.shadowed_keys == ("font_size",)
    (cfg / "kitty.conf").write_text(f"{INCLUDE_BEGIN}\n")
    assert adapter.read_state().bootstrap == "weird"


def test_install_rejects_foreign_plan(
    env: tuple[KittyAdapter, FakeRunner, Path],
) -> None:
    adapter, _, cfg = env
    with pytest.raises(ValueError):
        adapter.install(base.BootstrapPlan(file=cfg / "other.conf", line="x"))
    with pytest.raises(ValueError):
        adapter.install(base.BootstrapPlan(file=adapter.user_file, line="include /x"))


def test_validate(env: tuple[KittyAdapter, FakeRunner, Path]) -> None:
    adapter, _, _ = env

    def s(key: str, value: bool | int | float | str) -> list[str]:
        op = SetOp(op="set", target="kitty", key=key, value=value)
        return [p.code for p in adapter.validate(op)]

    assert s("font_size", 12.0) == []
    assert s("font_size", 500.0) == ["out_of_range"]
    assert s("nope", 1) == ["unknown_key"]
    assert s("color3", "#112233") == []
    assert s("color3", "red") == ["bad_color"]
    assert s("cursor_shape", "star") == ["bad_enum"]
    assert s("font_family", "A\nmap x y") == ["bad_value"]
    other = SetOp(op="set", target="alacritty", key="font_size", value=12.0)
    assert adapter.validate(other)[0].code == "wrong_target"
    ui = FontOp(op="font", target="kitty", role="ui", family="X")
    assert adapter.validate(ui)[0].code == "bad_role"
    bad = PaletteOp(op="palette", palette="no-such", variant=None, targets=["kitty"])
    assert adapter.validate(bad)[0].code == "bad_palette"
    not_us = PaletteOp(op="palette", palette="nord", variant=None, targets=["waybar"])
    assert adapter.validate(not_us)[0].code == "wrong_target"


def test_render_skips_invalid_ops(env: tuple[KittyAdapter, FakeRunner, Path]) -> None:
    adapter, _, _ = env
    op = SetOp(op="set", target="kitty", key="font_family", value="A\nmap x y")
    text = adapter.render([op], TargetState()).files[adapter.managed_file]
    assert "map" not in text


def test_detect(env: tuple[KittyAdapter, FakeRunner, Path]) -> None:
    adapter, runner, cfg = env
    runner.results[("kitty", "--version")] = CommandResult(
        0, "kitty 0.35.2 created by Kovid Goyal\n"
    )
    runner.results[("pgrep", "-x", "-U", UID, "kitty")] = CommandResult(0, "42\n")
    det = adapter.detect()
    assert (det.installed, det.version, det.running) == (True, "0.35.2", True)
    assert det.config_dir == cfg
    runner.results[("kitty", "--version")] = CommandError("missing")
    assert not adapter.detect().installed


def test_reload(env: tuple[KittyAdapter, FakeRunner, Path]) -> None:
    adapter, runner, _ = env
    argv = ("pkill", "-USR1", "-x", "-U", UID, "kitty")
    assert adapter.reload().ok
    assert runner.calls[-1][0] == argv
    runner.results[argv] = CommandResult(1)
    assert adapter.reload().ok  # nothing running: not a failure
    runner.results[argv] = CommandResult(2)
    assert not adapter.reload().ok
    runner.results[argv] = CommandError("no pkill")
    res = adapter.reload()
    assert not res.ok and "ctrl+shift+f5" in res.message


def test_managed_files_never_include_the_users_kitty_conf() -> None:
    # Design 3 and 5.4: managed_files is the general write allowlist; the user's
    # main config is only ever the separately declared bootstrap file.
    names = {p.name for p in KittyAdapter.managed_files}
    assert names == {"buckwheat.conf"}


def test_allowed_keys_and_state_updates(
    env: tuple[KittyAdapter, FakeRunner, Path],
) -> None:
    adapter, _, _ = env
    keys = adapter.allowed_keys()
    assert keys == sorted(keys) and "background_opacity" in keys
    updates = adapter.state_updates(PLAN_OPS, adapter.read_state())
    assert updates["background_opacity"] == 0.9
    assert updates["font_family"] == "Fira Code"
    assert set(updates) <= set(keys)
    # Saved updates alone reproduce the render: a later change keeps the palette.
    state = base.TargetState(values=dict(updates), bootstrap="bootstrapped")
    assert adapter.render([], state) == adapter.render(PLAN_OPS, state)
    # Ops for other targets are ignored.
    other = SetOp(op="set", target="alacritty", key="background_opacity", value=0.5)
    assert adapter.state_updates([other], adapter.read_state()) == {}
