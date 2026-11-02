import os
import tomllib
from pathlib import Path

import pytest

from buckwheat.adapters import alacritty, base
from buckwheat.adapters.alacritty import (
    PALETTE_SLOTS,
    REGISTRY,
    AlacrittyAdapter,
    format_managed,
    toml_str,
)
from buckwheat.adapters.base import (
    INCLUDE_BEGIN,
    MANAGED_HEADER,
    BootstrapPlan,
    CommandError,
    CommandResult,
    FakeRunner,
    RenderResult,
    TargetState,
)
from buckwheat.plan.models import FontOp, Op, PaletteOp, SetOp, WallpaperOp
from buckwheat.plan.palettes import list_palettes, load_palette
from buckwheat.safety import atomic_write, safe_delete

FIXTURES = Path(__file__).parent / "fixtures" / "alacritty"
V013 = {("alacritty", "--version"): CommandResult(0, "alacritty 0.13.2 (abc123)\n")}
V014 = {("alacritty", "--version"): CommandResult(0, "alacritty 0.14.0 (abc123)\n")}


def golden(name: str) -> str:
    return (FIXTURES / name).read_bytes().decode()


def make(
    home: Path, fixture: str | None = None, runner: FakeRunner | None = None
) -> AlacrittyAdapter:
    ad = AlacrittyAdapter(
        runner or FakeRunner(V014),
        user_conf=home / ".config" / "alacritty" / "alacritty.toml",
        managed_conf=home / ".config" / "alacritty" / "buckwheat.toml",
        state_path=home / "state" / "managed.json",
        home=home,
        which=lambda name: f"/usr/bin/{name}",
    )
    ad.user_conf.parent.mkdir(parents=True)
    if fixture:
        ad.user_conf.write_bytes((FIXTURES / fixture).read_bytes())
    return ad


def apply(ad: AlacrittyAdapter, res: RenderResult) -> None:
    """Write like the session does: only through atomic_write and the allowlist."""
    allow = [ad.user_conf, ad.managed_conf]
    assert set(res.files) <= set(allow)
    for path, text in res.files.items():
        atomic_write(path, text, allow)
    for path in res.deletes:
        safe_delete(path, [ad.managed_conf])  # never the user's file


def palette_op(name: str = "nord", variant: str | None = None) -> PaletteOp:
    return PaletteOp(op="palette", palette=name, variant=variant, targets=["alacritty"])


def font_op(family: str = "JetBrains Mono", size: float | None = 12.0) -> FontOp:
    return FontOp(
        op="font", target="alacritty", role="terminal", family=family, size=size
    )


def set_op(key: str, value: bool | int | float | str) -> SetOp:
    return SetOp(op="set", target="alacritty", key=key, value=value)


def codes(ad: AlacrittyAdapter, op: Op) -> list[str]:
    return [p.code for p in ad.validate(op)]


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sys_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stands in for an $XDG_CONFIG_DIRS entry (normally /etc/xdg)."""
    path = tmp_path / "sys-xdg"
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(path))
    return path


@pytest.fixture
def etc_conf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stands in for /etc/alacritty/alacritty.toml."""
    path = tmp_path / "etc" / "alacritty" / "alacritty.toml"
    monkeypatch.setattr(alacritty, "ETC_CONF", path)
    return path


@pytest.fixture(autouse=True)
def isolated_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sys_xdg: Path, etc_conf: Path
) -> None:
    """Keep every test off the real home and /etc: system paths go to tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


# --- registry, emitter -----------------------------------------------------


def test_registered() -> None:
    assert base.get("alacritty") is AlacrittyAdapter
    assert "alacritty" in base.available()


def test_managed_files_never_include_the_users_alacritty_toml() -> None:
    # Design 3 and 5.4: managed_files is the general write allowlist; the user's
    # main config is only ever the separately declared bootstrap file.
    names = {p.name for p in AlacrittyAdapter.managed_files}
    assert names == {"buckwheat.toml"}


@pytest.mark.parametrize(
    "text", ["plain", 'quo"te', "back\\slash", "tab\there", "nl\nx", "\x00\x7f", "é✓"]
)
def test_toml_str_round_trips(text: str) -> None:
    assert tomllib.loads(f"k = {toml_str(text)}") == {"k": text}


def test_palette_slots_resolve_for_every_palette_variant() -> None:
    assert set(PALETTE_SLOTS) <= set(REGISTRY)
    for info in list_palettes():
        pv = load_palette(info.name)
        values = format_managed({k: _slot(pv, s) for k, s in PALETTE_SLOTS.items()})
        parsed = tomllib.loads(values)
        assert parsed["colors"]["primary"]["background"] == pv.colors.bg


def _slot(pv: object, slot: str) -> str:
    group, name = slot.split(".")
    return str(getattr(getattr(pv, group), name))


# --- render ----------------------------------------------------------------


def test_render_golden_palette_and_font(home: Path) -> None:
    ad = make(home, "pristine.toml")
    ops: list[Op] = [
        palette_op("nord"),
        font_op(),
        set_op("window.opacity", 0.95),
        set_op("window.padding.x", 8),
        set_op("window.padding.y", 6),
    ]
    res = ad.render(ops, ad.read_state())
    assert res.files == {ad.managed_conf: golden("golden_nord_font.toml")}


def test_render_golden_gruvbox_light_variant(home: Path) -> None:
    ad = make(home)
    res = ad.render([palette_op("gruvbox", "light")], ad.read_state())
    assert res.files == {ad.managed_conf: golden("golden_gruvbox_light.toml")}


def test_render_empty_state_is_header_only(home: Path) -> None:
    ad = make(home)
    assert ad.render([], ad.read_state()).files == {
        ad.managed_conf: MANAGED_HEADER + "\n"
    }


def test_rendered_output_is_valid_toml_with_expected_values(home: Path) -> None:
    ad = make(home)
    text = ad.render(
        [font_op("Fira Code", 9), set_op("window.opacity", 1)], ad.read_state()
    )
    parsed = tomllib.loads(text.files[ad.managed_conf])
    assert parsed["font"]["normal"]["family"] == "Fira Code"
    assert parsed["font"]["size"] == 9.0
    assert parsed["window"]["opacity"] == 1.0


def test_idempotent_reapply(home: Path) -> None:
    ad = make(home, "pristine.toml")
    ops: list[Op] = [palette_op(), font_op(), set_op("window.opacity", 0.9)]
    first = ad.render(ops, ad.read_state())
    apply(ad, first)
    base.save_managed_state(
        {"alacritty": {"font.size": 12.0, "window.opacity": 0.9}}, ad.state_path
    )
    # Same ops again, on top of the state the first apply left behind.
    again = ad.render(ops, ad.read_state())
    assert again == first
    # No ops at all: the previous managed values are kept, not dropped.
    kept = ad.render([], ad.read_state())
    assert "opacity = 0.9" in kept.files[ad.managed_conf]
    assert ad.render(ops, ad.read_state()) == ad.render(ops, ad.read_state())


def test_render_merges_with_previous_state_and_later_ops_win(home: Path) -> None:
    ad = make(home)
    base.save_managed_state(
        {"alacritty": {"font.size": 10.0, "window.opacity": 0.5}}, ad.state_path
    )
    res = ad.render([set_op("window.opacity", 0.8)], ad.read_state())
    parsed = tomllib.loads(res.files[ad.managed_conf])
    assert parsed["font"]["size"] == 10.0
    assert parsed["window"]["opacity"] == 0.8
    res = ad.render([set_op("font.size", 9), set_op("font.size", 14)], ad.read_state())
    assert tomllib.loads(res.files[ad.managed_conf])["font"]["size"] == 14.0


def test_stale_or_hostile_state_values_are_dropped(home: Path) -> None:
    ad = make(home)
    base.save_managed_state(
        {
            "alacritty": {
                "font.normal.family": 'x"\nshell = "evil',
                "terminal.shell": "/bin/sh",
                "window.opacity": 7.0,
                "font.size": 11.0,
            }
        },
        ad.state_path,
    )
    res = ad.render([], ad.read_state())
    assert res.files[ad.managed_conf] == f"{MANAGED_HEADER}\n\n[font]\nsize = 11.0\n"


def test_render_only_returns_the_managed_file(home: Path) -> None:
    ad = make(home, "pristine.toml")
    res = ad.render([palette_op()], ad.read_state())
    assert list(res.files) == [ad.managed_conf]


# --- user config untouched --------------------------------------------------


@pytest.mark.parametrize(
    "fixture", ["pristine.toml", "user_shadowed.toml", "no_trailing_newline_crlf.toml"]
)
def test_user_config_untouched_by_everything_but_install(
    home: Path, fixture: str
) -> None:
    runner = FakeRunner(V014)
    ad = make(home, fixture, runner)
    before = ad.user_conf.read_bytes()
    mtime = os.stat(ad.user_conf).st_mtime_ns
    ad.detect()
    state = ad.read_state()
    res = ad.render([palette_op(), font_op()], state)
    apply(ad, res)  # writes buckwheat.toml only
    ad.validate(set_op("font.size", 12))
    ad.reload()
    ad.read_state()
    assert ad.user_conf.read_bytes() == before
    assert os.stat(ad.user_conf).st_mtime_ns == mtime
    assert not any(c[0][0] not in ("alacritty", "pgrep") for c in runner.calls)


# --- shadow detection -------------------------------------------------------


def test_shadow_detected_and_reported(home: Path) -> None:
    ad = make(home, "user_shadowed.toml")
    state = ad.read_state()
    assert state.bootstrap == "none"  # not included yet, but already reported
    assert state.shadowed_keys == (
        "font.size",
        "font.normal.family",
        "window.opacity",
        "colors.primary.background",
        "colors.cursor.cursor",
    )
    apply(ad, ad.install(ad.bootstrap_plan()))
    state = ad.read_state()
    assert state.bootstrap == "user-shadowed"
    assert state.shadowed_keys == (
        "font.size",
        "font.normal.family",
        "window.opacity",
        "colors.primary.background",
        "colors.cursor.cursor",
    )


def test_shadow_does_not_change_user_value_or_block_render(home: Path) -> None:
    ad = make(home, "user_shadowed.toml")
    before = ad.user_conf.read_bytes()
    res = ad.render([set_op("font.size", 15)], ad.read_state())
    assert "size = 15.0" in res.files[ad.managed_conf]
    assert ad.user_conf.read_bytes() == before
    assert "font.size" in ad.read_state().shadowed_keys


def test_no_shadow_when_user_sets_unrelated_keys(home: Path) -> None:
    ad = make(home, "pristine.toml")
    apply(ad, ad.install(ad.bootstrap_plan()))
    state = ad.read_state()
    assert state.shadowed_keys == ()
    assert state.bootstrap == "bootstrapped"


def test_shadow_via_inline_and_dotted_tables(home: Path) -> None:
    ad = make(home)
    ad.user_conf.write_text("font = { size = 9 }\nwindow.padding.x = 3\n")
    assert ad.read_state().shadowed_keys == ("font.size", "window.padding.x")


def test_unparsable_user_file_is_weird(home: Path) -> None:
    ad = make(home, "unparsable.toml")
    assert ad.read_state().bootstrap == "weird"
    with pytest.raises(ValueError):
        ad.install(ad.bootstrap_plan())


def test_missing_user_file_is_none(home: Path) -> None:
    ad = make(home)
    state = ad.read_state()
    assert (state.bootstrap, state.shadowed_keys) == ("none", ())


# --- install / uninstall ----------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "expected", "runner_results"),
    [
        ("pristine.toml", "installed_pristine.toml", V014),
        ("general_table.toml", "installed_general_table.toml", V014),
        (
            "no_trailing_newline_crlf.toml",
            "installed_no_trailing_newline_crlf.toml",
            V014,
        ),
        ("existing_import.toml", "installed_existing_import.toml", V014),
        ("legacy_top_level.toml", "installed_legacy_top_level.toml", V013),
        ("legacy_top_level.toml", "installed_legacy_top_level_new.toml", V014),
    ],
)
def test_install_golden_and_exact_uninstall(
    home: Path,
    fixture: str,
    expected: str,
    runner_results: dict[tuple[str, ...], CommandResult],
) -> None:
    ad = make(home, fixture, FakeRunner(runner_results))
    original = ad.user_conf.read_bytes()
    res = ad.install(ad.bootstrap_plan())
    assert res.files[ad.user_conf] == golden(expected)
    assert res.files[ad.managed_conf].startswith(MANAGED_HEADER)
    apply(ad, res)
    assert ad.read_state().bootstrap in ("bootstrapped", "user-shadowed")

    # Install is idempotent.
    assert ad.install(ad.bootstrap_plan()).files[ad.user_conf] == golden(expected)

    # Uninstall restores the user's file byte for byte and deletes ours.
    out = ad.uninstall()
    assert out.deletes == {ad.managed_conf}
    assert ad.managed_conf not in out.files
    assert out.files[ad.user_conf].encode() == original
    apply(ad, out)
    assert ad.user_conf.read_bytes() == original
    assert not ad.managed_conf.exists()
    assert ad.read_state().bootstrap == "none"
    assert ad.uninstall().files == {}


def test_install_creates_user_file_when_absent_and_uninstall_empties_it(
    home: Path,
) -> None:
    ad = make(home)
    res = ad.install(ad.bootstrap_plan())
    assert res.files[ad.user_conf] == golden("installed_absent.toml")
    apply(ad, res)
    out = ad.uninstall()
    # The user's file is never deleted, only emptied; ours is deleted.
    assert out.files[ad.user_conf] == ""
    assert out.deletes == {ad.managed_conf}


def test_install_result_parses_and_lists_the_import(home: Path) -> None:
    ad = make(home, "general_table.toml")
    text = ad.install(ad.bootstrap_plan()).files[ad.user_conf]
    assert INCLUDE_BEGIN in text
    data = tomllib.loads(text)
    assert data["general"]["import"] == ["~/.config/alacritty/buckwheat.toml"]
    assert data["general"]["live_config_reload"] is True
    assert data["window"] == {"decorations": "none"}


@pytest.mark.parametrize(
    "fixture", ["multiline_import.toml", "dotted_general.toml", "unparsable.toml"]
)
def test_install_refuses_files_it_cannot_edit_safely(home: Path, fixture: str) -> None:
    ad = make(home, fixture)
    before = ad.user_conf.read_bytes()
    with pytest.raises(ValueError):
        ad.install(ad.bootstrap_plan())
    assert ad.user_conf.read_bytes() == before


def test_install_rejects_mismatched_plan(home: Path) -> None:
    ad = make(home, "pristine.toml")
    good = ad.bootstrap_plan()
    with pytest.raises(ValueError):
        ad.install(BootstrapPlan(Path("/etc/passwd"), good.line))
    with pytest.raises(ValueError):
        ad.install(BootstrapPlan(good.file, 'import = ["/tmp/evil.toml"]'))


def test_user_owned_buckwheat_toml_is_never_replaced(home: Path) -> None:
    ad = make(home, "pristine.toml")
    ad.managed_conf.write_text("# mine\n[font]\nsize = 3\n")
    assert "unsupported" in codes(ad, set_op("font.size", 12))
    with pytest.raises(ValueError):
        ad.render([set_op("font.size", 12)], ad.read_state())
    with pytest.raises(ValueError):
        ad.install(ad.bootstrap_plan())
    assert ad.uninstall().files == {}


def test_uninstall_without_include_changes_nothing(home: Path) -> None:
    ad = make(home, "pristine.toml")
    assert ad.uninstall().files == {}


# --- validate ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "code"),
    [
        (set_op("terminal.shell", "/bin/sh"), "unknown-key"),
        (set_op("keyboard.bindings", "x"), "unknown-key"),
        (set_op("hints.enabled", "x"), "unknown-key"),
        (set_op("general.import", "/tmp/evil.toml"), "unknown-key"),
        (set_op("font.size", 500), "out_of_range"),
        (set_op("font.size", True), "bad_type"),
        (set_op("font.size", "big"), "bad_type"),
        (set_op("window.opacity", 1.5), "out_of_range"),
        (set_op("window.padding.x", 2.5), "bad_type"),
        (set_op("colors.primary.background", "red"), "bad-color"),
        (set_op("colors.normal.red", "red"), "bad-value"),
        (set_op("colors.normal.red", "#12345"), "bad-color"),
        (set_op("colors.normal.red", 5), "bad_type"),
        (set_op("font.normal.family", "A\nshell = 1"), "control-chars"),
        (set_op("font.normal.family", 'A"B'), "bad-value"),
        (
            SetOp(op="set", target="kitty", key="font.size", value=12),
            "wrong-target",
        ),
        (
            FontOp(op="font", target="alacritty", role="ui", family="Inter"),
            "unsupported-role",
        ),
        (
            FontOp(op="font", target="alacritty", role="terminal", family="A;B"),
            "bad-font",
        ),
        (
            PaletteOp(op="palette", palette="nord", targets=["kitty"]),
            "wrong-target",
        ),
        (palette_op("no-such-palette"), "unknown-palette"),
        (palette_op("nord", "nope"), "unknown-palette"),
        (WallpaperOp(op="wallpaper", path="~/Pictures/a.png"), "unsupported-op"),
    ],
)
def test_validate_rejects(home: Path, op: Op, code: str) -> None:
    ad = make(home)
    assert code in codes(ad, op)
    with pytest.raises(ValueError):
        ad.render([op], ad.read_state())


def test_validate_accepts_good_ops(home: Path) -> None:
    ad = make(home)
    for op in (
        palette_op(),
        font_op(),
        font_op("Hack", None),
        set_op("font.size", 12),
        set_op("window.opacity", 0),
        set_op("colors.bright.red", "#ABCDEF"),
        set_op("window.padding.y", 100),
    ):
        assert ad.validate(op) == [], op


def test_no_registered_key_runs_programs() -> None:
    forbidden = ("shell", "hints", "bindings", "import", "exec", "program", "args")
    for key in REGISTRY:
        assert not any(word in key for word in forbidden), key


# --- detect / reload --------------------------------------------------------


def test_detect_reports_version_and_running(home: Path) -> None:
    runner = FakeRunner(V014)
    det = make(home, None, runner).detect()
    assert (det.installed, det.version, det.running) == (True, "0.14.0", True)
    assert det.config_dir == home / ".config" / "alacritty"
    assert [c[0] for c in runner.calls] == [
        ("alacritty", "--version"),
        ("pgrep", "-x", "alacritty"),
    ]


def test_detect_not_running_and_command_errors(home: Path) -> None:
    runner = FakeRunner(
        {
            ("alacritty", "--version"): CommandError("boom"),
            ("pgrep", "-x", "alacritty"): CommandResult(1),
        }
    )
    det = make(home, None, runner).detect()
    assert (det.installed, det.version, det.running) == (True, None, False)


def test_detect_not_installed(home: Path) -> None:
    ad = make(home)
    ad.which = lambda name: None
    runner = FakeRunner()
    ad.runner = runner
    assert not ad.detect().installed
    assert runner.calls == []


def test_reload_is_a_noop_that_checks_live_reload(home: Path) -> None:
    runner = FakeRunner()
    ad = make(home, "pristine.toml", runner)
    res = ad.reload()
    assert res.ok and "reloads" in res.message
    ad.user_conf.write_bytes((FIXTURES / "live_reload_off.toml").read_bytes())
    res = ad.reload()
    assert res.ok and "live_config_reload is off" in res.message
    assert runner.calls == []


def test_allowed_keys_and_state_updates(tmp_path: Path) -> None:
    ad = make(tmp_path, "pristine.toml")
    keys = ad.allowed_keys()
    assert keys == sorted(keys) and "window.opacity" in keys
    ops: list[Op] = [palette_op(), font_op(), set_op("window.opacity", 0.9)]
    updates = ad.state_updates(ops, ad.read_state())
    assert updates["window.opacity"] == 0.9
    assert updates["font.normal.family"] == "JetBrains Mono"
    assert set(updates) <= set(keys)
    # Saved updates alone reproduce the render: a later change keeps the palette.
    state = TargetState(values=dict(updates), bootstrap="bootstrapped")
    assert ad.render([], state) == ad.render(ops, state)


# --- config dir: XDG_CONFIG_HOME and ~/.config -------------------------------


@pytest.fixture
def xdg(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """HOME and XDG_CONFIG_HOME both point into tmp_path, never the real home."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    return home / "xdg"


def env_adapter(home: Path) -> AlacrittyAdapter:
    """An adapter with no explicit paths, so its paths come from the environment."""
    return AlacrittyAdapter(
        FakeRunner(V014), home=home, which=lambda name: f"/usr/bin/{name}"
    )


def test_default_paths_without_xdg_config_home(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    ad = env_adapter(home)
    assert ad.user_conf == home / ".config" / "alacritty" / "alacritty.toml"
    assert ad.managed_conf == home / ".config" / "alacritty" / "buckwheat.toml"


def test_xdg_config_home_is_used_when_set(home: Path, xdg: Path) -> None:
    ad = env_adapter(home)
    assert ad.user_conf == xdg / "alacritty" / "alacritty.toml"
    assert ad.managed_conf == xdg / "alacritty" / "buckwheat.toml"
    assert ad.bootstrap_files() == [xdg / "alacritty" / "alacritty.toml"]
    assert ad.detect().config_dir == xdg / "alacritty"


@pytest.mark.parametrize("value", ["", "relative/dir"])
def test_unusable_xdg_config_home_falls_back_to_dot_config(
    home: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", value)
    ad = env_adapter(home)
    assert ad.user_conf == home / ".config" / "alacritty" / "alacritty.toml"


def test_existing_dot_config_file_is_kept_when_xdg_has_none(
    home: Path, xdg: Path
) -> None:
    # alacritty reads ~/.config/alacritty/alacritty.toml here. An include written
    # under XDG would create a file alacritty reads first and hide the user's config.
    user = home / ".config" / "alacritty" / "alacritty.toml"
    user.parent.mkdir(parents=True)
    user.write_bytes((FIXTURES / "pristine.toml").read_bytes())
    ad = env_adapter(home)
    assert ad.user_conf == user
    apply(ad, ad.install(ad.bootstrap_plan()))
    assert ad.read_state().bootstrap == "bootstrapped"
    assert not xdg.exists()


def test_xdg_file_wins_over_dot_config_file(home: Path, xdg: Path) -> None:
    for conf in (xdg / "alacritty", home / ".config" / "alacritty"):
        conf.mkdir(parents=True)
        (conf / "alacritty.toml").write_text("")
    assert env_adapter(home).user_conf == xdg / "alacritty" / "alacritty.toml"


def test_xdg_alacritty_dir_wins_over_top_level_xdg_file(home: Path, xdg: Path) -> None:
    (xdg / "alacritty").mkdir(parents=True)
    for conf in (xdg / "alacritty" / "alacritty.toml", xdg / "alacritty.toml"):
        conf.write_text("")
    assert env_adapter(home).user_conf == xdg / "alacritty" / "alacritty.toml"


def test_top_level_xdg_file_is_used_and_not_shadowed(home: Path, xdg: Path) -> None:
    # alacritty reads $XDG_CONFIG_HOME/alacritty.toml when nothing above it exists.
    # An include written under xdg/alacritty/ would be read first and hide it.
    xdg.mkdir(parents=True)
    user = xdg / "alacritty.toml"
    user.write_bytes((FIXTURES / "pristine.toml").read_bytes())
    ad = env_adapter(home)
    assert ad.user_conf == user
    assert ad.managed_conf == xdg / "buckwheat.toml"
    apply(ad, ad.install(ad.bootstrap_plan()))
    assert ad.read_state().bootstrap == "bootstrapped"
    assert not (xdg / "alacritty").exists()


def test_install_with_xdg_config_home_writes_only_there(home: Path, xdg: Path) -> None:
    (xdg / "alacritty").mkdir(parents=True)
    ad = env_adapter(home)
    res = ad.install(ad.bootstrap_plan())
    assert set(res.files) == {ad.user_conf, ad.managed_conf}
    apply(ad, res)
    data = tomllib.loads(ad.user_conf.read_text())
    assert data["general"]["import"] == ["~/xdg/alacritty/buckwheat.toml"]
    assert ad.read_state().bootstrap == "bootstrapped"
    assert not (home / ".config").exists()


# --- which file alacritty reads --------------------------------------------


def write_pristine(path: Path) -> Path:
    """Create ``path``, and its parents, holding the pristine user config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((FIXTURES / "pristine.toml").read_bytes())
    return path


def expect_refusal(ad: AlacrittyAdapter, read: Path) -> None:
    """install() must refuse, and name ``read``, the file alacritty reads."""
    with pytest.raises(ValueError, match="alacritty reads") as exc:
        ad.install(ad.bootstrap_plan())
    assert str(read) in str(exc.value)


def test_effective_conf_follows_alacritty_order(
    home: Path, xdg: Path, sys_xdg: Path, etc_conf: Path
) -> None:
    order = [
        xdg / "alacritty" / "alacritty.toml",
        sys_xdg / "alacritty" / "alacritty.toml",
        xdg / "alacritty.toml",
        sys_xdg / "alacritty.toml",
        home / ".config" / "alacritty" / "alacritty.toml",
        home / ".alacritty.toml",
        etc_conf,
    ]
    assert alacritty._effective_conf() is None
    # Add the files last-first: each file added wins over those already there.
    for path in reversed(order):
        write_pristine(path)
        assert alacritty._effective_conf() == path


@pytest.mark.parametrize(
    ("value", "dirs"),
    [
        (None, ["/etc/xdg"]),
        ("", ["/etc/xdg"]),
        ("/opt/a:relative:/opt/b::", ["/opt/a", "/opt/b"]),
    ],
)
def test_xdg_config_dirs_keeps_absolute_entries(
    monkeypatch: pytest.MonkeyPatch, value: str | None, dirs: list[str]
) -> None:
    if value is None:
        monkeypatch.delenv("XDG_CONFIG_DIRS", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_DIRS", value)
    assert alacritty._xdg_config_dirs() == [Path(d) for d in dirs]


def test_install_refuses_a_lone_home_dot_file(home: Path, xdg: Path) -> None:
    dot = write_pristine(home / ".alacritty.toml")
    ad = env_adapter(home)
    expect_refusal(ad, dot)
    assert not ad.user_conf.exists()
    assert dot.read_bytes() == (FIXTURES / "pristine.toml").read_bytes()


def test_install_refuses_a_system_file_from_xdg_config_dirs(
    home: Path, xdg: Path, sys_xdg: Path
) -> None:
    system = write_pristine(sys_xdg / "alacritty" / "alacritty.toml")
    ad = env_adapter(home)
    expect_refusal(ad, system)
    assert not ad.user_conf.exists()


def test_install_refuses_a_system_file_ahead_of_the_user_file(
    home: Path, xdg: Path, sys_xdg: Path
) -> None:
    # XDG_CONFIG_HOME is elsewhere, so install would edit ~/.config/alacritty.
    # alacritty reads the system file first, so that edit would never apply.
    user = write_pristine(home / ".config" / "alacritty" / "alacritty.toml")
    system = write_pristine(sys_xdg / "alacritty" / "alacritty.toml")
    ad = env_adapter(home)
    assert ad.user_conf == user
    expect_refusal(ad, system)
    assert user.read_bytes() == (FIXTURES / "pristine.toml").read_bytes()
    assert not ad.managed_conf.exists()


def test_install_refuses_the_etc_master_file(
    home: Path, xdg: Path, etc_conf: Path
) -> None:
    master = write_pristine(etc_conf)
    ad = env_adapter(home)
    expect_refusal(ad, master)
    assert not ad.user_conf.exists()


def test_install_creates_the_xdg_file_when_alacritty_reads_nothing(
    home: Path, xdg: Path
) -> None:
    ad = env_adapter(home)
    res = ad.install(ad.bootstrap_plan())
    assert set(res.files) == {ad.user_conf, ad.managed_conf}
    assert ad.user_conf == xdg / "alacritty" / "alacritty.toml"
    data = tomllib.loads(res.files[ad.user_conf])
    assert data["general"]["import"] == ["~/xdg/alacritty/buckwheat.toml"]


def test_install_edits_the_xdg_file_alacritty_reads(home: Path, xdg: Path) -> None:
    user = write_pristine(xdg / "alacritty" / "alacritty.toml")
    ad = env_adapter(home)
    assert ad.user_conf == user
    apply(ad, ad.install(ad.bootstrap_plan()))
    assert ad.read_state().bootstrap == "bootstrapped"


def test_explicit_user_conf_is_not_checked(home: Path) -> None:
    # A caller that passes user_conf chose that file on purpose, so alacritty's
    # own choice (here ~/.alacritty.toml) is not a reason to refuse.
    write_pristine(home / ".alacritty.toml")
    ad = make(home)
    res = ad.install(ad.bootstrap_plan())
    assert ad.user_conf in res.files


# --- alacritty.yml: read only when no alacritty.toml exists -----------------


def write_yaml(path: Path) -> Path:
    """Create ``path``, and its parents, holding a YAML config (never parsed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("font:\n  size: 12\n")
    return path


def test_effective_conf_yaml_follows_alacritty_order(
    home: Path, xdg: Path, sys_xdg: Path, etc_conf: Path
) -> None:
    order = [
        xdg / "alacritty" / "alacritty.yml",
        sys_xdg / "alacritty" / "alacritty.yml",
        xdg / "alacritty.yml",
        sys_xdg / "alacritty.yml",
        home / ".config" / "alacritty" / "alacritty.yml",
        home / ".alacritty.yml",
        etc_conf.parent / "alacritty.yml",
    ]
    assert alacritty._effective_conf() is None
    # Add the files last-first: each file added wins over those already there.
    for path in reversed(order):
        write_yaml(path)
        assert alacritty._effective_conf() == path


def test_toml_anywhere_in_the_order_wins_over_yaml(home: Path, xdg: Path) -> None:
    # The YAML file is first in alacritty's order, but alacritty looks for YAML only
    # when no alacritty.toml exists. So the TOML file further down is the one read.
    write_yaml(xdg / "alacritty" / "alacritty.yml")
    dot = write_pristine(home / ".alacritty.toml")
    ad = env_adapter(home)
    assert alacritty._effective_conf() == dot
    expect_refusal(ad, dot)  # as today: a file that is not the user's own
    assert not ad.user_conf.exists()


def test_users_toml_wins_over_yaml_earlier_in_the_order(home: Path, xdg: Path) -> None:
    write_yaml(xdg / "alacritty" / "alacritty.yml")
    user = write_pristine(home / ".config" / "alacritty" / "alacritty.toml")
    ad = env_adapter(home)
    assert ad.user_conf == user
    assert alacritty._effective_conf() == user
    apply(ad, ad.install(ad.bootstrap_plan()))
    assert ad.read_state().bootstrap == "bootstrapped"


def expect_yaml_refusal(ad: AlacrittyAdapter, yml: Path) -> None:
    """install() must refuse and write nothing; the message names ``yml``."""
    before = yml.read_bytes()
    with pytest.raises(ValueError, match="alacritty migrate") as exc:
        ad.install(ad.bootstrap_plan())
    assert f"alacritty reads {yml}" in str(exc.value)
    assert yml.read_bytes() == before
    assert not ad.user_conf.exists()
    assert not ad.managed_conf.exists()


def test_install_refuses_a_yaml_config_in_dot_config(home: Path) -> None:
    yml = write_yaml(home / ".config" / "alacritty" / "alacritty.yml")
    expect_yaml_refusal(env_adapter(home), yml)


def test_install_refuses_a_yaml_config_in_xdg_config_home(
    home: Path, xdg: Path
) -> None:
    yml = write_yaml(xdg / "alacritty" / "alacritty.yml")
    expect_yaml_refusal(env_adapter(home), yml)


def test_install_refuses_a_lone_home_dot_yaml_file(home: Path, xdg: Path) -> None:
    yml = write_yaml(home / ".alacritty.yml")
    expect_yaml_refusal(env_adapter(home), yml)


def test_explicit_user_conf_is_not_checked_for_yaml(home: Path) -> None:
    ad = make(home)  # the caller picked user_conf, so the lookup does not apply
    write_yaml(home / ".config" / "alacritty" / "alacritty.yml")
    res = ad.install(ad.bootstrap_plan())
    assert ad.user_conf in res.files
