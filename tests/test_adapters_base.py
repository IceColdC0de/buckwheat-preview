import json
from pathlib import Path
from typing import ClassVar

import pytest

from buckwheat.adapters import base
from buckwheat.adapters.base import (
    CommandError,
    CommandResult,
    FakeRunner,
    KeySpec,
    SubprocessRunner,
)
from buckwheat.plan.models import TargetName


def test_fake_runner_records_calls() -> None:
    runner = FakeRunner({("a", "b"): CommandResult(3, "out")})
    assert runner.run(["a", "b"], timeout=2).returncode == 3
    assert runner.run(["c"]).returncode == 0
    assert runner.calls == [(("a", "b"), 2), (("c",), base.DEFAULT_TIMEOUT)]


def test_fake_runner_raises_canned_error() -> None:
    runner = FakeRunner({("x",): CommandError("boom")})
    with pytest.raises(CommandError):
        runner.run(["x"])


def test_fake_runner_spawn() -> None:
    runner = FakeRunner({("bad",): CommandError("failed to spawn")})
    runner.spawn(["good", "arg"])
    assert runner.spawned == [("good", "arg")]
    assert runner.spawn_calls == [("good", "arg")]
    with pytest.raises(CommandError):
        runner.spawn(["bad"])
    with pytest.raises(CommandError):
        runner.spawn([])


def test_subprocess_runner_real_and_errors() -> None:
    runner = SubprocessRunner()
    res = runner.run(["echo", "$HOME; ls"])
    assert res.stdout == "$HOME; ls\n"  # no shell expansion
    with pytest.raises(CommandError):
        runner.run(["definitely-not-a-binary-xyz"])
    with pytest.raises(CommandError):
        runner.run(["sleep", "5"], timeout=0.1)
    with pytest.raises(CommandError):
        runner.run([])


def test_subprocess_runner_spawn() -> None:
    runner = SubprocessRunner()
    runner.spawn(["true"])
    with pytest.raises(CommandError):
        runner.spawn(["definitely-not-a-binary-xyz"])
    with pytest.raises(CommandError):
        runner.spawn([])


def test_keyspec_checks() -> None:
    f = KeySpec(float, min=0, max=1)
    assert f.check("k", 0.5) == []
    assert f.check("k", 2)[0].code == "out_of_range"
    assert f.check("k", True)[0].code == "bad_type"
    assert f.check("k", "x")[0].code == "bad_type"
    e = KeySpec(str, enum=("top", "bottom"))
    assert e.check("p", "top") == []
    assert e.check("p", "left")[0].code == "bad_enum"
    assert KeySpec(int).check("n", 1.5)[0].code == "bad_type"
    assert KeySpec(bool).check("b", 1)[0].code == "bad_type"


def test_managed_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "managed.json"
    assert base.load_managed_state(path) == {}
    state: base.ManagedState = {"kitty": {"background_opacity": 0.9, "x": "y"}}
    base.save_managed_state(state, path)
    assert base.load_managed_state(path) == state
    assert json.loads(path.read_text())["kitty"]["x"] == "y"


def test_managed_state_ignores_garbage(tmp_path: Path) -> None:
    path = tmp_path / "managed.json"
    path.write_text("not json")
    assert base.load_managed_state(path) == {}
    path.write_text('{"kitty": {"a": [1], "b": 2}, "w": 3}')
    assert base.load_managed_state(path) == {"kitty": {"b": 2}}


def test_merge_state() -> None:
    assert base.merge_state({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


class _Dummy:
    name: ClassVar[TargetName] = "kitty"
    managed_files: ClassVar[list[Path]] = []


def test_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    base.register("kitty")(_Dummy)  # type: ignore[arg-type]
    assert base.available() == ["kitty"]
    assert base.get("kitty") is _Dummy
    with pytest.raises(ValueError):
        base.register("kitty")(_Dummy)  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        base.get("nope")
