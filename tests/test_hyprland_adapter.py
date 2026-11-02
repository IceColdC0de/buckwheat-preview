import shutil
from pathlib import Path

import pytest

from buckwheat.adapters import base
from buckwheat.adapters.base import (
    CommandError,
    CommandResult,
    FakeRunner,
    save_managed_state,
)
from buckwheat.adapters.hyprland import HyprlandAdapter
from buckwheat.plan.models import FontOp, PaletteOp, SetOp, WallpaperOp
from buckwheat.safety.atomic import atomic_write, safe_delete

FIXTURES = Path(__file__).parent / "fixtures" / "hyprland"
RELOAD = ("hyprctl", "reload")
ERRORS = ("hyprctl", "configerrors")


def make(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    runner: FakeRunner | None = None,
) -> HyprlandAdapter:
    home = tmp_path / "home"
    conf = home / ".config" / "hypr"
    shutil.copytree(FIXTURES / scenario, conf)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    return HyprlandAdapter(runner or FakeRunner(), config_dir=conf)


def apply(adapter: HyprlandAdapter, files: dict[Path, str]) -> None:
    """Write like the session does: only through atomic_write + allowlist."""
    allow = [adapter.managed_path, adapter.main_path]
    for path, text in files.items():
        atomic_write(path, text, allow)


def set_op(key: str, value: bool | int | float | str) -> SetOp:
    return SetOp(op="set", target="hyprland", key=key, value=value)


def golden(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_registered() -> None:
    assert base.get("hyprland") is HyprlandAdapter


def test_render_golden_set_ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    ops = [
        set_op("general.gaps_in", 4),
        set_op("general.col.active_border", "#88C0D0"),
        set_op("decoration.rounding", 10),
        set_op("decoration.active_opacity", 0.95),
        set_op("decoration.blur.enabled", True),
        set_op("decoration.blur.size", 3),
        set_op("animations.enabled", False),
    ]
    out = a.render(list(ops), a.read_state()).files
    assert out == {a.managed_path: golden("set_ops.golden")}


def test_render_golden_palette(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    op = PaletteOp(op="palette", palette="nord", targets=["hyprland"])
    out = a.render([op], a.read_state()).files
    assert out == {a.managed_path: golden("palette_nord.golden")}


def test_idempotent_reapply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    ops = [set_op("general.gaps_in", 4), set_op("decoration.blur.enabled", True)]
    first = a.render(list(ops), a.read_state())
    apply(a, dict(first.files))
    save_managed_state(
        {"hyprland": {"general.gaps_in": 4, "decoration.blur.enabled": True}}
    )
    second = a.render(list(ops), a.read_state())
    assert second.files == first.files
    # Rendering with no ops reproduces the same file from stored state.
    assert a.render([], a.read_state()).files == first.files
    # Overlay: later ops win, other values are kept.
    third = a.render([set_op("general.gaps_in", 9)], a.read_state()).files
    text = third[a.managed_path]
    assert "gaps_in = 9" in text and "enabled = true" in text


def test_user_config_untouched_by_render_and_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make(tmp_path, monkeypatch, "bootstrapped")
    before = a.main_path.read_bytes()
    res = a.render([set_op("general.gaps_in", 4)], a.read_state())
    assert list(res.files) == [a.managed_path]
    apply(a, dict(res.files))
    assert a.main_path.read_bytes() == before
    assert a.managed_path.read_text() == res.files[a.managed_path]


def test_write_outside_allowlist_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    with pytest.raises(Exception, match="allowlist"):
        atomic_write(a.main_path, "x", [a.managed_path])


def test_reload_ok_and_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeRunner({ERRORS: CommandResult(0, "")})
    a = make(tmp_path, monkeypatch, "pristine", runner)
    assert a.reload().ok
    # ("hyprctl", "version") only tells the config format; classic here.
    calls = [c[0] for c in runner.calls if c[0] != ("hyprctl", "version")]
    assert calls == [RELOAD, ERRORS]


def test_reload_configerrors_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = "config error in file buckwheat.conf at line 3: invalid field gaps_inn"
    runner = FakeRunner({ERRORS: CommandResult(0, out + "\n")})
    a = make(tmp_path, monkeypatch, "pristine", runner)
    res = a.reload()
    assert not res.ok
    assert "gaps_inn" in res.message


def test_reload_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = FakeRunner({RELOAD: CommandResult(1, "", "boom")})
    assert not make(tmp_path, monkeypatch, "pristine", bad).reload().ok
    missing = FakeRunner({RELOAD: CommandError("hyprctl: not found")})
    assert not HyprlandAdapter(missing, config_dir=tmp_path).reload().ok
    ok_text = FakeRunner({ERRORS: CommandResult(0, "no errors\n")})
    assert HyprlandAdapter(ok_text, config_dir=tmp_path).reload().ok


def test_validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    assert a.validate(set_op("general.gaps_in", 4)) == []
    assert a.validate(set_op("general.gaps_in", 4.5))[0].code == "bad_type"
    assert a.validate(set_op("general.gaps_in", 9999))[0].code == "out_of_range"
    assert a.validate(set_op("general.exec-once", "x"))[0].code == "unknown_key"
    assert a.validate(set_op("general.col.active_border", "red"))[0].code == "bad_color"
    assert a.validate(set_op("decoration.blur.enabled", 1))[0].code == "bad_type"
    bad_pal = PaletteOp(op="palette", palette="nope", targets=["hyprland"])
    assert a.validate(bad_pal)[0].code == "bad_palette"
    font = FontOp(op="font", target="hyprland", role="ui", family="Inter")
    assert a.validate(font)[0].code == "unsupported"
    assert a.validate(WallpaperOp(op="wallpaper", path="/x.png")) == []
    with pytest.raises(ValueError):
        a.render([set_op("nope", 1)], a.read_state())


def test_render_ignores_other_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    other = SetOp(op="set", target="kitty", key="font_size", value=12)
    res = a.render([other], a.read_state())
    assert res.files[a.managed_path].count("\n") == 1  # header only


def test_detect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeRunner({("hyprctl", "version"): CommandResult(0, "Hyprland 0.41.2")})
    d = make(tmp_path, monkeypatch, "pristine", runner).detect()
    assert (d.installed, d.running, d.version) == (True, True, "0.41.2")
    gone = FakeRunner({("hyprctl", "version"): CommandError("nope")})
    d2 = HyprlandAdapter(gone, config_dir=tmp_path / "none").detect()
    assert not d2.installed and not d2.running


@pytest.mark.parametrize("scenario", ["pristine", "weird"])
def test_install_uninstall_roundtrip_byte_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    a = make(tmp_path, monkeypatch, scenario)
    original = a.main_path.read_bytes()
    had_managed = a.managed_path.exists()
    assert a.read_state().bootstrap == "none"
    res = a.install(a.bootstrap_plan())
    assert set(res.files) == {a.main_path, a.managed_path}
    apply(a, dict(res.files))
    assert a.read_state().bootstrap == "bootstrapped"
    assert a.main_path.read_bytes().startswith(original.rstrip(b"\n"))
    removed = a.uninstall()
    assert removed.deletes == {a.managed_path}
    assert removed.leftovers == ()
    apply(a, dict(removed.files))
    for path in removed.deletes:
        safe_delete(path, [a.managed_path])
    assert a.main_path.read_bytes() == original
    assert a.managed_path.exists() == had_managed


def test_uninstall_reports_source_line_left_after_the_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    original = a.main_path.read_text()
    assert original.endswith("\n")
    installed = a.install(a.bootstrap_plan()).files[a.main_path]
    bare = f"{a.include_line()}\n"
    a.main_path.write_text(installed + bare)

    removed = a.uninstall()
    # The marked block is still removed; only the stray line stays.
    assert removed.files[a.main_path] == original + bare
    assert len(removed.leftovers) == 1
    message = removed.leftovers[0]
    assert "hyprland.conf" in message and f"`{a.include_line()}`" in message
    assert "by hand" in message


@pytest.mark.parametrize("spelling", ["install", "no-spaces"])
def test_uninstall_reports_bare_source_line_without_a_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    original = a.main_path.read_text()
    line = a.include_line() if spelling == "install" else f"source={a.managed_path}"
    a.main_path.write_text(original + line + "\n")
    before = a.main_path.read_bytes()

    removed = a.uninstall()
    assert a.main_path not in removed.files  # nothing to strip: file unchanged
    assert len(removed.leftovers) == 1 and line in removed.leftovers[0]
    apply(a, dict(removed.files))
    assert a.main_path.read_bytes() == before


def test_uninstall_ignores_source_lines_for_other_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    other = "source = ~/other/buckwheat.conf\nsource = ~/.config/hypr/colors.conf\n"
    a.main_path.write_text(a.main_path.read_text() + other)
    assert a.uninstall().leftovers == ()


def test_install_golden_and_bootstrapped_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    text = a.install(a.bootstrap_plan()).files[a.main_path]
    home_rel = a.include_line()
    assert home_rel == "source = ~/.config/hypr/buckwheat.conf"
    assert text == (FIXTURES / "bootstrapped" / "hyprland.conf").read_text()
    b = make(tmp_path / "b", monkeypatch, "bootstrapped")
    assert b.read_state().bootstrap == "bootstrapped"
    with pytest.raises(ValueError):
        b.install(b.bootstrap_plan())  # already installed
    assert (
        b.uninstall().files[b.main_path]
        == (FIXTURES / "pristine" / "hyprland.conf").read_text()
    )


def test_install_rejects_foreign_plan_and_weird_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    plan = a.bootstrap_plan()
    with pytest.raises(ValueError):
        a.install(type(plan)(file=plan.file, line="exec = rm -rf ~"))
    a.main_path.write_text("# >>> buckwheat (managed include) >>>\n")
    assert a.read_state().bootstrap == "weird"


def test_allowed_keys_and_state_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make(tmp_path, monkeypatch, "pristine")
    assert "general.gaps_in" in a.allowed_keys()
    assert a.allowed_keys() == sorted(a.allowed_keys())
    ops = [
        PaletteOp(op="palette", palette="nord", targets=["hyprland"]),
        set_op("general.gaps_in", 4),
    ]
    assert a.state_updates(ops, a.read_state()) == {
        "general.col.active_border": "#88c0d0",
        "general.col.inactive_border": "#434c5e",
        "general.gaps_in": 4,
    }
    # Ops for other targets are ignored.
    other = PaletteOp(op="palette", palette="nord", targets=["kitty"])
    assert a.state_updates([other], a.read_state()) == {}
