import json
import os
import shutil
from pathlib import Path

import pytest

from buckwheat.adapters import base
from buckwheat.adapters.base import CommandError, CommandResult, FakeRunner
from buckwheat.adapters.waybar import WaybarAdapter
from buckwheat.plan.models import FontOp, PaletteOp, SetOp

FIXTURES = Path(__file__).parent / "fixtures" / "waybar"
UID = str(os.getuid())
PGREP = ("pgrep", "-U", UID, "-x", "waybar")
PKILL = ("pkill", "-USR2", "-U", UID, "-x", "waybar")


def make(tmp_path: Path, scenario: str, runner: FakeRunner | None = None):
    cfg = tmp_path / "waybar"
    shutil.copytree(FIXTURES / scenario, cfg)
    return WaybarAdapter(cfg, runner or FakeRunner(), sleep=lambda _s: None), cfg


def snapshot(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


def write(files: base.RenderResult) -> None:
    for path, text in files.files.items():
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    for path in files.deletes:
        assert path.name in ("buckwheat.jsonc", "buckwheat.css")
        path.unlink()


OPS = [
    PaletteOp(op="palette", palette="nord", targets=["waybar"]),
    SetOp(op="set", target="waybar", key="bar.height", value=32),
    SetOp(op="set", target="waybar", key="bar.position", value="bottom"),
    FontOp(op="font", target="waybar", role="bar", family="JetBrains Mono", size=12),
]


def golden(name: str) -> str:
    with open(FIXTURES / "golden" / name, encoding="utf-8", newline="") as f:
        return f.read()


def test_render_matches_golden(tmp_path: Path) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    result = adapter.render(list(OPS), adapter.read_state())
    assert result.files[cfg / "buckwheat.jsonc"] == golden("buckwheat.jsonc")
    assert result.files[cfg / "buckwheat.css"] == golden("buckwheat.css")


def test_render_does_not_touch_disk(tmp_path: Path) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    before = snapshot(cfg)
    adapter.render(list(OPS), adapter.read_state())
    assert snapshot(cfg) == before


def test_reapply_is_idempotent(tmp_path: Path) -> None:
    adapter, _ = make(tmp_path, "pristine")
    first = adapter.render(list(OPS), adapter.read_state())
    again = WaybarAdapter(
        adapter.dir, FakeRunner(), saved={"bar.height": 32, "bar.position": "bottom"}
    )
    # Same ops on top of state that already holds some of the values.
    assert again.render(list(OPS), again.read_state()) == first
    assert adapter.render(list(OPS), adapter.read_state()) == first


def test_previous_values_survive_new_ops(tmp_path: Path) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    adapter = WaybarAdapter(adapter.dir, FakeRunner(), saved={"bar.height": 30})
    ops = [SetOp(op="set", target="waybar", key="bar.spacing", value=4)]
    text = adapter.render(ops, adapter.read_state()).files[cfg / "buckwheat.jsonc"]
    assert '"height": 30' in text and '"spacing": 4' in text


def test_other_targets_ignored(tmp_path: Path) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    ops = [SetOp(op="set", target="kitty", key="font_size", value=12)]
    text = adapter.render(ops, adapter.read_state()).files[cfg / "buckwheat.jsonc"]
    assert text.endswith("{}\n")


@pytest.mark.parametrize("scenario", ["pristine", "weird"])
def test_install_uninstall_round_trip_and_user_files(
    tmp_path: Path, scenario: str
) -> None:
    adapter, cfg = make(tmp_path, scenario)
    original = snapshot(cfg)
    plan = adapter.bootstrap_plan()
    assert plan is not None and adapter.read_state().bootstrap != "bootstrapped"
    installed = adapter.install(plan)
    assert set(installed.files) == {plan.file, cfg / "style.css"}
    write(installed)
    assert adapter.read_state().bootstrap == "bootstrapped"
    assert "buckwheat.jsonc" in plan.file.read_text()
    write(adapter.render(OPS, adapter.read_state()))  # creates our two files
    assert {"buckwheat.jsonc", "buckwheat.css"} <= set(snapshot(cfg))
    removed = adapter.uninstall()
    assert removed.deletes == {adapter.json_path, adapter.css_path}
    assert removed.leftovers == ()
    write(removed)
    assert snapshot(cfg) == original  # byte-for-byte, CRLF and no-newline included
    assert adapter.read_state().bootstrap == "none"


def test_uninstall_reports_stray_include_lines_left_after_the_blocks(
    tmp_path: Path,
) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    plan = adapter.bootstrap_plan()
    assert plan is not None
    write(adapter.install(plan))
    write(adapter.render(OPS, adapter.read_state()))
    # Keep only the include entry (no markers) in the config, and only the
    # import (no markers) in style.css, as if the markers were deleted by hand.
    config = cfg / "config.jsonc"
    entry = f'  "include": [{json.dumps(str(adapter.json_path))}],\n'
    config.write_text(config.read_text().replace("{\n", "{\n" + entry, 1))
    css_import = '@import url("buckwheat.css");\n'
    css = (cfg / "style.css").read_text()
    (cfg / "style.css").write_text(css + css_import)
    # The blocks are removed; the stray lines stay where they were.
    original_cfg = (FIXTURES / "pristine" / "config.jsonc").read_text()
    original_css = (FIXTURES / "pristine" / "style.css").read_text()
    removed = adapter.uninstall()
    assert removed.files[config] == original_cfg.replace("{\n", "{\n" + entry, 1)
    assert removed.files[adapter.style_path] == original_css + css_import
    assert len(removed.leftovers) == 2
    names = " ".join(removed.leftovers)
    assert "config.jsonc" in names and "style.css" in names
    assert all("by hand" in message for message in removed.leftovers)
    assert "buckwheat.css" in names and "buckwheat.jsonc" in names


def test_uninstall_reports_bare_lines_and_leaves_files_unchanged(
    tmp_path: Path,
) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    config = cfg / "config.jsonc"
    entry = f'  "include": [{json.dumps(str(adapter.json_path))}],\n'
    config.write_text(config.read_text().replace("{\n", "{\n" + entry, 1))
    (cfg / "style.css").write_text(
        (cfg / "style.css").read_text() + '@import url("buckwheat.css");\n'
    )
    before = snapshot(cfg)

    removed = adapter.uninstall()
    assert not removed.files and not removed.deletes  # no block, nothing to delete
    assert len(removed.leftovers) == 2
    write(removed)
    assert snapshot(cfg) == before


def test_uninstall_ignores_include_of_another_file(tmp_path: Path) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    (cfg / "style.css").write_text('@import url("other/buckwheat.css");\n')
    config = cfg / "config.jsonc"
    other = '  "include": ["/elsewhere/buckwheat.jsonc"],\n'
    config.write_text(config.read_text().replace("{\n", "{\n" + other, 1))
    assert adapter.uninstall().leftovers == ()


def test_install_goldens(tmp_path: Path) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    plan = adapter.bootstrap_plan()
    assert plan is not None
    files = adapter.install(plan).files
    # Goldens use a placeholder for the tmp dir.
    norm = {p.name: t.replace(str(cfg), "<WAYBAR>") for p, t in files.items()}
    assert norm["config.jsonc"] == golden("config.jsonc.installed")
    assert norm["style.css"] == golden("style.css.installed")


def test_install_refuses_own_include(tmp_path: Path) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    (cfg / "config.jsonc").write_text('{ "include": ["x.json"] }')
    plan = adapter.bootstrap_plan()
    assert plan is not None
    with pytest.raises(ValueError):
        adapter.install(plan)


def test_install_empty_object_has_no_trailing_comma(tmp_path: Path) -> None:
    adapter, cfg = make(tmp_path, "pristine")
    (cfg / "config.jsonc").write_text("{}")
    plan = adapter.bootstrap_plan()
    assert plan is not None
    text = adapter.install(plan).files[plan.file]
    assert 'buckwheat.jsonc"]\n' in text.replace("\r", "")
    write(adapter.uninstall())  # nothing installed yet: no-op
    assert (cfg / "config.jsonc").read_text() == "{}"


def test_validate_rejects_bad_ops(tmp_path: Path) -> None:
    adapter, _ = make(tmp_path, "pristine")

    def codes(op: SetOp | FontOp | PaletteOp) -> list[str]:
        return [p.code for p in adapter.validate(op)]

    assert codes(SetOp(op="set", target="waybar", key="bar.height", value=500)) == [
        "out_of_range"
    ]
    assert codes(SetOp(op="set", target="waybar", key="exec", value="x")) == [
        "unknown_key"
    ]
    assert codes(SetOp(op="set", target="waybar", key="color.bg", value="red")) == [
        "bad_color"
    ]
    assert codes(
        SetOp(op="set", target="waybar", key="font_family", value='a"; x{')
    ) == ["bad_font"]
    assert codes(FontOp(op="font", target="waybar", role="terminal", family="x")) == [
        "bad_role"
    ]
    assert codes(PaletteOp(op="palette", palette="nope", targets=["waybar"])) == [
        "unknown_palette"
    ]
    assert codes(SetOp(op="set", target="kitty", key="whatever", value=1)) == []


def test_render_raises_on_invalid_ops(tmp_path: Path) -> None:
    adapter, _ = make(tmp_path, "pristine")
    with pytest.raises(ValueError):
        adapter.render(
            [SetOp(op="set", target="waybar", key="color.bg", value="x;")],
            adapter.read_state(),
        )


def test_detect(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("waybar", "--version"): CommandResult(0, "Waybar v0.10.4\n"),
            PGREP: CommandResult(0, "123\n"),
        }
    )
    adapter, cfg = make(tmp_path, "pristine", runner)
    d = adapter.detect()
    assert (d.installed, d.version, d.running, d.config_dir) == (
        True,
        "0.10.4",
        True,
        cfg,
    )
    missing = FakeRunner({("waybar", "--version"): CommandError("nope")})
    adapter2, _ = make(tmp_path / "b", "pristine", missing)
    assert not adapter2.detect().installed


def test_reload_signals_and_checks_alive(tmp_path: Path) -> None:
    runner = FakeRunner({PGREP: CommandResult(0), PKILL: CommandResult(0)})
    adapter, _ = make(tmp_path, "pristine", runner)
    assert adapter.reload().ok
    assert [c[0] for c in runner.calls] == [PGREP, PKILL, PGREP]


def test_reload_not_running_is_noop(tmp_path: Path) -> None:
    runner = FakeRunner({PGREP: CommandResult(1)})
    adapter, _ = make(tmp_path, "pristine", runner)
    assert adapter.reload().ok
    assert [c[0] for c in runner.calls] == [PGREP]


def test_reload_dead_after_signal_fails(tmp_path: Path) -> None:
    class Dying(FakeRunner):
        def run(self, argv, timeout=base.DEFAULT_TIMEOUT):  # type: ignore[no-untyped-def]
            res = super().run(argv, timeout)
            if tuple(argv) == PKILL:
                self.results[PGREP] = CommandResult(1)
            return res

    runner = Dying({PGREP: CommandResult(0), PKILL: CommandResult(0)})
    adapter, _ = make(tmp_path, "pristine", runner)
    assert not adapter.reload().ok


def test_failed_reload_then_revert_restarts_waybar_once(tmp_path: Path) -> None:
    class Dying(FakeRunner):
        def run(self, argv, timeout=base.DEFAULT_TIMEOUT):  # type: ignore[no-untyped-def]
            res = super().run(argv, timeout)
            if tuple(argv) == PKILL:
                self.results[PGREP] = CommandResult(1)
            return res

    runner = Dying({PGREP: CommandResult(0), PKILL: CommandResult(0)})
    adapter, _ = make(tmp_path, "pristine", runner)
    # 1. Reload while running -> process dies -> reload fails
    res1 = adapter.reload()
    assert not res1.ok
    assert "waybar exited after reload" in (res1.message or "")
    assert runner.spawned == []

    # 2. Revert triggers second reload -> restarts waybar
    res2 = adapter.reload()
    assert res2.ok
    assert runner.spawned == [("waybar",)]

    # 3. Third reload when not running does not spawn again
    res3 = adapter.reload()
    assert res3.ok
    assert runner.spawned == [("waybar",)]


def test_reload_restart_failure(tmp_path: Path) -> None:
    class Dying(FakeRunner):
        def run(self, argv, timeout=base.DEFAULT_TIMEOUT):  # type: ignore[no-untyped-def]
            res = super().run(argv, timeout)
            if tuple(argv) == PKILL:
                self.results[PGREP] = CommandResult(1)
            return res

    runner = Dying(
        {
            PGREP: CommandResult(0),
            PKILL: CommandResult(0),
            ("waybar",): CommandError("permission denied"),
        }
    )
    adapter, _ = make(tmp_path, "pristine", runner)
    assert not adapter.reload().ok
    res = adapter.reload()
    assert not res.ok
    assert "could not restart waybar" in (res.message or "")


def test_reload_not_running_never_restarts(tmp_path: Path) -> None:
    runner = FakeRunner({PGREP: CommandResult(1)})
    adapter, _ = make(tmp_path, "pristine", runner)
    assert adapter.reload().ok
    assert adapter.reload().ok
    assert runner.spawned == []


def test_registered() -> None:
    assert base.get("waybar") is WaybarAdapter
    assert all(p.parent.name == "waybar" for p in WaybarAdapter.managed_files)


def test_allowed_keys_and_state_updates(tmp_path: Path) -> None:
    adapter, _ = make(tmp_path, "pristine")
    keys = adapter.allowed_keys()
    assert keys == sorted(keys) and "bar.height" in keys and "color.bg" in keys
    updates = adapter.state_updates(list(OPS), adapter.read_state())
    assert updates["bar.height"] == 32
    assert updates["font_family"] == "JetBrains Mono"
    assert updates["font_size"] == 12
    assert updates["color.accent"] == "#88c0d0"
    assert set(updates) <= set(keys)
    # Rendering from the saved updates alone reproduces the golden files.
    state = base.TargetState(values=updates, bootstrap="bootstrapped")
    assert adapter.render([], state) == adapter.render(list(OPS), state)
