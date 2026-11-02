import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

import buckwheat
from buckwheat import cli

SUBPACKAGES = ["plan", "providers", "adapters", "safety", "tui"]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    assert importlib.import_module(f"buckwheat.{name}") is not None


def test_config_imports() -> None:
    assert importlib.import_module("buckwheat.config") is not None


def test_version_is_set() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", buckwheat.__version__)


def test_run_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.run(["--version"]) == 0
    assert capsys.readouterr().out == f"buckwheat {buckwheat.__version__}\n"


def test_console_script_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "buckwheat.main", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == f"buckwheat {buckwheat.__version__}\n"


def test_run_without_version_flag_launches_the_tui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launched: list[object] = []
    from buckwheat.tui import app as tui_app

    monkeypatch.setattr(tui_app.BuckwheatApp, "run", lambda self: launched.append(self))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert cli.run([]) == 0
    assert len(launched) == 1


def test_bad_config_is_reported_not_launched(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert cli.run(["--config", str(tmp_path / "missing.toml")]) == 2
    assert "config file not found" in capsys.readouterr().err
