from pathlib import Path

import pytest

from buckwheat.adapters import base
from buckwheat.adapters.base import CommandError, CommandResult, FakeRunner
from buckwheat.adapters.wallpaper import WallpaperAdapter
from buckwheat.plan.models import FontOp, WallpaperOp

FIXTURES = Path(__file__).parent / "fixtures" / "wallpaper"


def golden(name: str, path: Path) -> str:
    return (FIXTURES / name).read_text().replace("{path}", str(path))


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "Pictures").mkdir()
    (tmp_path / "Pictures" / "forest.jpg").write_bytes(b"jpg")
    return tmp_path


def make(home: Path, runner: FakeRunner | None = None) -> WallpaperAdapter:
    return WallpaperAdapter(
        runner or FakeRunner(),
        conf_path=home / ".config" / "hypr" / "hyprpaper.conf",
        state_path=home / "state" / "managed.json",
        home=home,
    )


def op(path: str, mode: str = "fill") -> WallpaperOp:
    return WallpaperOp.model_validate({"op": "wallpaper", "path": path, "mode": mode})


def test_registered() -> None:
    assert base.get("wallpaper") is WallpaperAdapter


@pytest.mark.parametrize(
    ("mode", "name"), [("fill", "golden_fill.conf"), ("fit", "golden_fit.conf")]
)
def test_render_golden(home: Path, mode: str, name: str) -> None:
    ad = make(home)
    res = ad.render([op("~/Pictures/forest.jpg", mode)], ad.read_state())
    real = (home / "Pictures" / "forest.jpg").resolve()
    assert res.files == {ad.conf_path: golden(name, real)}


def test_render_is_deterministic(home: Path) -> None:
    ad = make(home)
    ops = [op("~/Pictures/forest.jpg")]
    assert ad.render(ops, ad.read_state()) == ad.render(ops, ad.read_state())


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("~/Pictures/../.ssh/id_rsa", "path-traversal"),
        ("/etc/passwd", "path-not-allowed"),
        ("~/Pictures/x.jpg\nexec = rm -rf ~", "control-chars"),
        ("relative.png", "bad-path"),
        ("~/Pictures/missing.png", "bad-path"),
    ],
)
def test_hostile_paths_rejected(home: Path, path: str, code: str) -> None:
    ad = make(home)
    assert code in [p.code for p in ad.validate(op(path))]
    with pytest.raises(ValueError):
        ad.render([op(path)], ad.read_state())


def test_symlink_escape_rejected(home: Path) -> None:
    secret = home / "secret.png"
    secret.write_bytes(b"x")
    (home / "Pictures" / "link.png").symlink_to(secret)
    assert "path-not-allowed" in [
        p.code for p in make(home).validate(op("~/Pictures/link.png"))
    ]


def test_bad_extension_and_mode(home: Path) -> None:
    (home / "Pictures" / "a.txt").write_text("x")
    ad = make(home)
    assert "bad-extension" in [p.code for p in ad.validate(op("~/Pictures/a.txt"))]
    assert "unsupported-mode" in [
        p.code for p in ad.validate(op("~/Pictures/forest.jpg", "center"))
    ]


def test_other_ops_rejected(home: Path) -> None:
    font = FontOp(op="font", target="kitty", role="terminal", family="Mono")
    assert make(home).validate(font)[0].code == "unsupported-op"


def test_user_owned_conf_is_not_edited(home: Path) -> None:
    ad = make(home)
    ad.conf_path.parent.mkdir(parents=True)
    ad.conf_path.write_text((FIXTURES / "user_owned.conf").read_text())
    state = ad.read_state()
    assert state.bootstrap == "user-shadowed"
    assert state.shadowed_keys == ("path", "mode")
    assert "unsupported" in [p.code for p in ad.validate(op("~/Pictures/forest.jpg"))]
    with pytest.raises(ValueError):
        ad.render([op("~/Pictures/forest.jpg")], state)
    assert ad.uninstall().files == {}
    with pytest.raises(ValueError):
        ad.install(base.BootstrapPlan(ad.conf_path, ""))


def test_managed_conf_can_be_rewritten_and_uninstalled(home: Path) -> None:
    ad = make(home)
    ad.conf_path.parent.mkdir(parents=True)
    ad.conf_path.write_text(base.MANAGED_HEADER + "\n")
    assert ad.read_state().bootstrap == "bootstrapped"
    assert ad.validate(op("~/Pictures/forest.jpg")) == []
    out = ad.uninstall()
    assert out.files == {}
    assert out.deletes == {ad.conf_path}


def test_read_state_uses_managed_state(home: Path) -> None:
    ad = make(home)
    base.save_managed_state(
        {"wallpaper": {"path": "/a.png", "mode": "fit"}}, ad.state_path
    )
    assert dict(ad.read_state().values) == {"path": "/a.png", "mode": "fit"}


def test_reload_runs_hyprctl_with_path_as_one_arg(home: Path) -> None:
    runner = FakeRunner()
    ad = make(home, runner)
    path = "/home/u/Pictures/my wall; x.png"
    base.save_managed_state({"wallpaper": {"path": path, "mode": "fit"}}, ad.state_path)
    assert ad.reload().ok
    assert [c[0] for c in runner.calls[:2]] == [
        ("hyprctl", "hyprpaper", "preload", path),
        ("hyprctl", "hyprpaper", "wallpaper", f",contain:{path}"),
    ]


def test_reload_failures(home: Path) -> None:
    ad = make(home, FakeRunner(default=CommandResult(0, "wallpaper failed")))
    base.save_managed_state({"wallpaper": {"path": "/a.png"}}, ad.state_path)
    assert not ad.reload().ok
    ad = make(home, FakeRunner(default=CommandResult(1, "", "boom")))
    assert not ad.reload().ok
    ad = make(
        home,
        FakeRunner({("hyprctl", "hyprpaper", "preload", "/a.png"): CommandError("x")}),
    )
    assert not ad.reload().ok


def test_reload_without_state_is_noop(tmp_path: Path) -> None:
    runner = FakeRunner()
    assert make(tmp_path, runner).reload().ok
    assert runner.calls == []


def test_detect(home: Path) -> None:
    runner = FakeRunner({("pgrep", "-x", "hyprpaper"): CommandResult(0)})
    ad = WallpaperAdapter(
        runner, conf_path=home / "c", home=home, which=lambda _: "/usr/bin/hyprpaper"
    )
    assert ad.detect().installed and ad.detect().running
    ad = WallpaperAdapter(runner, conf_path=home / "c", home=home, which=lambda _: None)
    assert not ad.detect().installed


def test_allowed_keys_and_state_updates(home: Path) -> None:
    ad = make(home)
    assert ad.allowed_keys() == ["mode", "path"]
    real = (home / "Pictures" / "forest.jpg").resolve()
    updates = ad.state_updates([op("~/Pictures/forest.jpg", "fit")], ad.read_state())
    assert updates == {"path": str(real), "mode": "fit"}
    assert ad.state_updates([], ad.read_state()) == {}
