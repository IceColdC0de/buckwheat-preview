"""``buckwheat check``: read-only, its report, and its exit code.

Everything runs against a temp HOME and XDG dirs. Commands go through FakeRunner,
the SDKs are stub modules, and PATH holds only the binaries a test creates.
"""

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from buckwheat import check, cli
from buckwheat.adapters import base
from buckwheat.adapters.base import (
    INCLUDE_BEGIN,
    INCLUDE_END,
    CommandError,
    CommandResult,
    FakeRunner,
)

# Shaped like a real key. It must never appear in any output.
SECRET = "sk-ant-api03-" + "A" * 40


@pytest.fixture
def desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A usable desktop with nothing set up yet. Returns the temp HOME.

    alacritty and hyprpaper are on PATH, so those targets are detected. The
    other targets are detected through the runner (see healthy_runner).
    """
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setitem(sys.modules, "anthropic", ModuleType("anthropic"))
    monkeypatch.setitem(sys.modules, "openai", ModuleType("openai"))
    for name in ("alacritty", "hyprpaper"):
        exe = bin_dir / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    return home


def healthy_runner() -> FakeRunner:
    """Each target answers its version probe; configerrors is not stubbed."""
    return FakeRunner(
        {
            ("kitty", "--version"): CommandResult(0, "kitty 0.42.1\n"),
            ("hyprctl", "version"): CommandResult(0, "Hyprland 0.45.2\n"),
            ("waybar", "--version"): CommandResult(0, "waybar v0.11.0\n"),
        }
    )


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    """Every path under root with its mtime and bytes (None for directories)."""
    return {
        str(p.relative_to(root)): (
            p.stat().st_mtime_ns,
            p.read_bytes() if p.is_file() else None,
        )
        for p in root.rglob("*")
    }


def test_healthy_desktop_exits_zero(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check.run_check(runner=healthy_runner()) == 0
    out = capsys.readouterr().out
    assert "api key: ANTHROPIC_API_KEY is set" in out
    assert "sdk: anthropic can be imported" in out
    assert "result: everything looks usable" in out


def test_reports_every_registered_adapter(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    check.run_check(runner=healthy_runner())
    lines = capsys.readouterr().out.splitlines()
    for name in base.available():
        assert name in lines  # each target has a header line
    # Adding an adapter to the registry must also add it to the check.
    assert {t.name for t in check._targets(FakeRunner())} == set(base.available())


def test_target_paths_follow_the_current_home(desk: Path) -> None:
    files = {
        t.name: [p for _, p in t.files() if p is not None]
        for t in check._targets(FakeRunner())
    }
    assert files["alacritty"] == [
        desk / ".config" / "alacritty" / "alacritty.toml",
        desk / ".config" / "alacritty" / "buckwheat.toml",
    ]
    assert files["wallpaper"] == [desk / ".config" / "hypr" / "hyprpaper.conf"]
    assert files["kitty"] == [
        desk / ".config" / "kitty" / "kitty.conf",
        desk / ".config" / "kitty" / "buckwheat.conf",
    ]
    assert files["hyprland"] == [
        desk / ".config" / "hypr" / "hyprland.conf",
        desk / ".config" / "hypr" / "buckwheat.conf",
    ]
    assert files["waybar"] == [
        desk / ".config" / "waybar" / "style.css",
        desk / ".config" / "waybar" / "buckwheat.jsonc",
        desk / ".config" / "waybar" / "buckwheat.css",
    ]


def test_alacritty_target_follows_xdg_config_home(
    desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # check shows the paths the adapter uses, so it moves with XDG_CONFIG_HOME.
    xdg = desk / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    target = next(t for t in check._targets(FakeRunner()) if t.name == "alacritty")
    assert [p for _, p in target.files()] == [
        xdg / "alacritty" / "alacritty.toml",
        xdg / "alacritty" / "buckwheat.toml",
    ]


def test_bootstrapped_include_and_managed_values_are_shown(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kitty_conf = write(
        desk / ".config" / "kitty" / "kitty.conf",
        f"font_size 11\n{INCLUDE_BEGIN}\ninclude buckwheat.conf\n{INCLUDE_END}\n",
    )
    write(
        desk / ".local" / "state" / "buckwheat" / "managed.json",
        json.dumps({"kitty": {"font_size": 12.0}}),
    )
    assert check.run_check(runner=healthy_runner()) == 0
    out = capsys.readouterr().out
    assert "bootstrapped: yes (status: bootstrapped)" in out
    assert "font_size = 12.0" in out
    assert f"user file: {kitty_conf} (exists)" in out


def test_user_keys_that_shadow_alacritty_are_reported_and_fail(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        desk / ".config" / "alacritty" / "alacritty.toml", "[window]\nopacity = 0.9\n"
    )
    assert check.run_check(runner=healthy_runner()) == 1
    out = capsys.readouterr().out
    assert "shadowed keys: window.opacity" in out
    assert "alacritty: your own config overrides window.opacity" in out


def test_a_half_written_include_block_is_a_problem(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(desk / ".config" / "kitty" / "kitty.conf", f"{INCLUDE_BEGIN}\n")
    assert check.run_check(runner=healthy_runner()) == 1
    out = capsys.readouterr().out
    assert "kitty: config is in a state buckwheat cannot manage (status: weird)" in out


def test_api_key_is_checked_by_name_and_never_printed(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check.run_check(runner=healthy_runner()) == 0
    out = capsys.readouterr().out
    assert "api key: ANTHROPIC_API_KEY is set" in out
    assert SECRET not in out


def test_the_configured_key_variable_name_is_the_one_checked(
    desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        desk / ".config" / "buckwheat" / "config.toml",
        'provider = "anthropic"\n\n[providers.anthropic]\napi_key_env = "MY_KEY"\n',
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("MY_KEY", SECRET)
    assert check.run_check(runner=healthy_runner()) == 0
    out = capsys.readouterr().out
    assert "api key: MY_KEY is set" in out
    assert SECRET not in out


def test_missing_api_key_is_a_problem(
    desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert check.run_check(runner=healthy_runner()) == 1
    out = capsys.readouterr().out
    assert "api key: ANTHROPIC_API_KEY is not set" in out
    assert "  - ANTHROPIC_API_KEY is not set" in out


def test_sdk_that_cannot_be_imported_is_a_problem(
    desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import now fails
    assert check.run_check(runner=healthy_runner()) == 1
    out = capsys.readouterr().out
    assert "sdk: anthropic cannot be imported (ModuleNotFoundError)" in out
    assert "  - the anthropic package cannot be imported" in out


def test_local_openai_compatible_endpoint_needs_no_key(
    desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        desk / ".config" / "buckwheat" / "config.toml",
        'provider = "openai_compat"\n\n[providers.openai_compat]\n'
        'model = "llama3"\nbase_url = "http://localhost:11434/v1"\n',
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert check.run_check(runner=healthy_runner()) == 0
    out = capsys.readouterr().out
    assert "provider: openai_compat" in out
    assert "api key: no environment variable configured" in out
    assert "sdk: openai can be imported" in out


def test_openai_compatible_provider_without_a_model_is_a_problem(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        desk / ".config" / "buckwheat" / "config.toml", 'provider = "openai_compat"\n'
    )
    assert check.run_check(runner=healthy_runner()) == 1
    out = capsys.readouterr().out
    assert "openai_compat needs a model in its config table" in out


def test_unreadable_config_is_reported_and_targets_are_still_checked(
    desk: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = write(tmp_path / "bad.toml", "provider = \n")
    assert check.run_check(str(bad), runner=healthy_runner()) == 1
    out = capsys.readouterr().out
    assert "invalid TOML" in out
    assert "provider: unknown" in out
    assert "\nkitty\n" in out


def test_no_installed_target_is_a_problem(
    desk: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("alacritty", "hyprpaper"):
        (tmp_path / "bin" / name).unlink()
    runner = FakeRunner(
        {
            ("kitty", "--version"): CommandError("no kitty"),
            ("waybar", "--version"): CommandError("no waybar"),
            ("hyprctl", "version"): CommandError("no hyprctl"),
        }
    )
    assert check.run_check(runner=runner) == 1
    assert "none of the targets is installed" in capsys.readouterr().out


def test_a_target_that_is_not_installed_is_not_a_problem(
    desk: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "bin" / "alacritty").unlink()
    assert check.run_check(runner=healthy_runner()) == 0
    out = capsys.readouterr().out
    assert "not installed (reported, not a problem): alacritty" in out


def test_hyprland_errors_are_printed_verbatim_and_fail(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = healthy_runner()
    runner.results[("hyprctl", "configerrors")] = CommandResult(
        0, "  line 3: unknown key 'foo'\n"
    )
    assert check.run_check(runner=runner, hyprland_errors=True) == 1
    out = capsys.readouterr().out
    assert "--- stdout ---\n  line 3: unknown key 'foo'\n--- end stdout ---" in out
    assert "hyprctl configerrors reports errors or failed" in out


@pytest.mark.parametrize("stdout", ["", "no errors\n", "No errors"])
def test_clean_hyprland_configerrors_passes(
    desk: Path, stdout: str, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = healthy_runner()
    runner.results[("hyprctl", "configerrors")] = CommandResult(0, stdout)
    assert check.run_check(runner=runner, hyprland_errors=True) == 0


def test_configerrors_only_runs_when_asked(desk: Path) -> None:
    runner = healthy_runner()
    check.run_check(runner=runner)
    assert ("hyprctl", "configerrors") not in [argv for argv, _ in runner.calls]


def test_check_writes_no_files_and_changes_nothing(
    desk: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(
        desk / ".config" / "kitty" / "kitty.conf",
        f"font_size 11\n{INCLUDE_BEGIN}\ninclude buckwheat.conf\n{INCLUDE_END}\n",
    )
    write(desk / ".config" / "alacritty" / "alacritty.toml", "[window]\nopacity = 1\n")
    write(
        desk / ".config" / "hypr" / "hyprland.conf",
        f"{INCLUDE_BEGIN}\nsource = ~/x.conf\n{INCLUDE_END}\n",
    )
    write(desk / ".config" / "waybar" / "config.jsonc", "{\n}\n")
    write(desk / ".config" / "waybar" / "style.css", "* {}\n")
    write(
        desk / ".local" / "state" / "buckwheat" / "managed.json",
        json.dumps({"kitty": {"font_size": 12.0}}),
    )
    before = snapshot(tmp_path)

    def no_writes(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("buckwheat check tried to write a file")

    monkeypatch.setattr(base, "atomic_write", no_writes)
    runner = healthy_runner()
    runner.results[("hyprctl", "configerrors")] = CommandResult(0, "")
    check.run_check(runner=runner, hyprland_errors=True)

    assert snapshot(tmp_path) == before
    commands = [argv for argv, _ in runner.calls]
    mutating = [
        argv
        for argv in commands
        if argv[0] == "pkill"
        or argv[:2] in (("hyprctl", "reload"), ("hyprctl", "hyprpaper"))
    ]
    assert mutating == []


def test_cli_check_dispatches_with_its_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str | None, bool]] = []

    def fake_run_check(
        config_path: str | None = None,
        *,
        hyprland_errors: bool = False,
        runner: object = None,
    ) -> int:
        calls.append((config_path, hyprland_errors))
        return 1

    monkeypatch.setattr(check, "run_check", fake_run_check)
    assert cli.run(["check", "--hyprland-errors"]) == 1  # the exit code passes through
    assert calls == [(None, True)]


@pytest.mark.parametrize("config_first", [True, False])
def test_cli_config_flag_works_before_or_after_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_first: bool
) -> None:
    calls: list[tuple[str | None, bool]] = []

    def fake_run_check(
        config_path: str | None = None,
        *,
        hyprland_errors: bool = False,
        runner: object = None,
    ) -> int:
        calls.append((config_path, hyprland_errors))
        return 0

    monkeypatch.setattr(check, "run_check", fake_run_check)
    cfg = str(tmp_path / "c.toml")
    argv = ["--config", cfg, "check"] if config_first else ["check", "--config", cfg]
    assert cli.run(argv) == 0
    assert calls == [(cfg, False)]
