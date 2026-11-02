import os
import stat
from pathlib import Path

import pytest

from buckwheat.safety import (
    PathNotAllowedError,
    atomic_write,
    guard_path,
    safe_delete,
    unified_diff,
)

needs_user = pytest.mark.skipif(os.geteuid() == 0, reason="refuses to run as root")


@pytest.fixture
def conf(tmp_path: Path) -> Path:
    d = tmp_path / "cfg"
    d.mkdir()
    return d / "app.conf"


def test_guard_allows_listed_path(conf: Path) -> None:
    assert guard_path(conf, [conf]) == conf.resolve()


def test_guard_rejects_unlisted(conf: Path) -> None:
    with pytest.raises(PathNotAllowedError):
        guard_path(conf.parent / "other.conf", [conf])


def test_guard_rejects_traversal(tmp_path: Path, conf: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("x")
    sneaky = conf.parent / ".." / "secret"
    with pytest.raises(PathNotAllowedError):
        guard_path(sneaky, [conf])


def test_guard_allows_dotdot_that_resolves_to_listed(conf: Path) -> None:
    assert guard_path(conf.parent / ".." / "cfg" / "app.conf", [conf]) == conf


def test_guard_rejects_symlink_escape(tmp_path: Path, conf: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    other = conf.parent / "other.conf"
    other.symlink_to(outside)
    with pytest.raises(PathNotAllowedError):
        guard_path(other, [conf])


def test_guard_rejects_symlinked_dir_escape(tmp_path: Path, conf: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (conf.parent / "link").symlink_to(outside)
    with pytest.raises(PathNotAllowedError):
        guard_path(conf.parent / "link" / "app.conf", [conf])


def test_guard_rejects_nul_byte(conf: Path) -> None:
    with pytest.raises(PathNotAllowedError):
        guard_path(str(conf) + "\0x", [conf])


def test_guard_symlinked_entry_resolves_inside_allowlist(
    tmp_path: Path, conf: Path
) -> None:
    # dotfile-manager layout: allowlisted path is a symlink to the real file
    real = tmp_path / "dotfiles" / "app.conf"
    real.parent.mkdir()
    real.write_text("old")
    conf.symlink_to(real)
    assert guard_path(conf, [conf]) == real.resolve()


@needs_user
def test_atomic_write_creates_file_with_default_mode(conf: Path) -> None:
    atomic_write(conf, "hello\n", [conf])
    assert conf.read_text() == "hello\n"
    assert stat.S_IMODE(conf.stat().st_mode) == 0o644
    assert [p.name for p in conf.parent.iterdir()] == ["app.conf"]


@needs_user
def test_atomic_write_preserves_existing_mode(conf: Path) -> None:
    conf.write_text("old")
    conf.chmod(0o600)
    atomic_write(conf, "new", [conf])
    assert conf.read_text() == "new"
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600


@needs_user
def test_atomic_write_follows_allowed_symlink(tmp_path: Path, conf: Path) -> None:
    real = tmp_path / "dotfiles" / "app.conf"
    real.parent.mkdir()
    real.write_text("old")
    conf.symlink_to(real)
    atomic_write(conf, "new", [conf])
    assert conf.is_symlink()
    assert real.read_text() == "new"


@needs_user
def test_atomic_write_rejects_unlisted_and_writes_nothing(
    tmp_path: Path, conf: Path
) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    with pytest.raises(PathNotAllowedError):
        atomic_write(victim, "pwned", [conf])
    assert victim.read_text() == "keep"


@needs_user
def test_atomic_write_rejects_symlink_escape(tmp_path: Path, conf: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    other = conf.parent / "other.conf"
    other.symlink_to(victim)
    with pytest.raises(PathNotAllowedError):
        atomic_write(other, "pwned", [conf])
    assert victim.read_text() == "keep"


@needs_user
def test_crash_before_rename_keeps_original(
    conf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf.write_text("original")

    def boom(src: object, dst: object) -> None:
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="simulated crash"):
        atomic_write(conf, "new content", [conf])
    assert conf.read_text() == "original"
    assert [p.name for p in conf.parent.iterdir()] == ["app.conf"]


@needs_user
def test_crash_during_fsync_keeps_original(
    conf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf.write_text("original")

    def boom(fd: int) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disk gone"):
        atomic_write(conf, "new content", [conf])
    assert conf.read_text() == "original"
    assert [p.name for p in conf.parent.iterdir()] == ["app.conf"]


def test_atomic_write_refuses_root(conf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(PermissionError):
        atomic_write(conf, "x", [conf])
    assert not conf.exists()


def test_unified_diff() -> None:
    d = unified_diff("a\nb\n", "a\nc\n", "x/app.conf")
    assert d.startswith("--- a/x/app.conf\n+++ b/x/app.conf\n")
    assert "-b\n" in d
    assert "+c\n" in d


def test_unified_diff_equal_is_empty() -> None:
    assert unified_diff("same\n", "same\n", "f") == ""


NO_NL = "\\ No newline at end of file\n"


def test_unified_diff_no_newline_old_side() -> None:
    d = unified_diff("a\nb", "a\nc\n", "f.py")
    assert d.endswith(f" a\n-b\n{NO_NL}+c\n")


def test_unified_diff_no_newline_new_side() -> None:
    d = unified_diff("a\nb\n", "a\nc", "f.py")
    assert d.endswith(f" a\n-b\n+c\n{NO_NL}")


def test_unified_diff_no_newline_both_sides() -> None:
    d = unified_diff("a\nb", "a\nc", "f.py")
    assert d.endswith(f" a\n-b\n{NO_NL}+c\n{NO_NL}")


def test_unified_diff_new_file() -> None:
    d = unified_diff("", "x\ny", "f.py")
    assert d.endswith(f"+x\n+y\n{NO_NL}")
    assert "-" not in d.split("@@")[-1]


def test_unified_diff_new_file_with_newline() -> None:
    assert unified_diff("", "x\n", "f.py").endswith("+x\n")


def test_unified_diff_deleted_file() -> None:
    d = unified_diff("x\ny", "", "f.py")
    assert d.endswith(f"-x\n-y\n{NO_NL}")


def test_guard_path_rejects_symlink_loop(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    with pytest.raises(PathNotAllowedError):
        guard_path(a, [a])


def test_guard_path_rejects_loop_inside_listed_directory(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    d.mkdir()
    a, b = d / "a", d / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    with pytest.raises(PathNotAllowedError):
        guard_path(a, [d])
    with pytest.raises(PathNotAllowedError):
        guard_path(a / "child", [d / "child"])


# --- safe_delete ------------------------------------------------------------


@needs_user
def test_safe_delete_removes_listed_file(conf: Path) -> None:
    conf.write_text("x")
    assert safe_delete(conf, [conf]) == conf.resolve()
    assert not conf.exists()


@needs_user
def test_safe_delete_missing_file_is_noop(conf: Path) -> None:
    assert safe_delete(conf, [conf]) is None


@needs_user
def test_safe_delete_rejects_unlisted(conf: Path) -> None:
    other = conf.parent / "user.conf"
    other.write_text("mine")
    with pytest.raises(PathNotAllowedError):
        safe_delete(other, [conf])
    assert other.read_text() == "mine"


@needs_user
def test_safe_delete_rejects_traversal(tmp_path: Path, conf: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("x")
    with pytest.raises(PathNotAllowedError):
        safe_delete(conf.parent / ".." / "secret", [conf])
    assert secret.exists()


@needs_user
def test_safe_delete_rejects_unlisted_symlink_to_elsewhere(
    tmp_path: Path, conf: Path
) -> None:
    secret = tmp_path / "secret"
    secret.write_text("x")
    link = conf.parent / "link.conf"
    link.symlink_to(secret)
    with pytest.raises(PathNotAllowedError):
        safe_delete(link, [conf])
    assert secret.exists() and link.is_symlink()


@needs_user
def test_safe_delete_follows_allowlisted_symlink_like_atomic_write(
    tmp_path: Path, conf: Path
) -> None:
    # stow-style: the managed path is a symlink into a dotfiles dir. Like
    # atomic_write, the real target is the file acted on (and snapshotted).
    real = tmp_path / "dotfiles" / "app.conf"
    real.parent.mkdir()
    real.write_text("x")
    conf.symlink_to(real)
    assert safe_delete(conf, [conf]) == real.resolve()
    assert not real.exists()


@needs_user
def test_safe_delete_refuses_directory(conf: Path) -> None:
    conf.mkdir()
    with pytest.raises(IsADirectoryError):
        safe_delete(conf, [conf])
    assert conf.is_dir()


def test_safe_delete_refuses_root(conf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conf.write_text("x")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(PermissionError):
        safe_delete(conf, [conf])
    assert conf.exists()


def test_diff_for_deleted_file() -> None:
    out = unified_diff("a\nb\n", "", "x.conf", deleted=True)
    assert out.startswith("--- a/x.conf\n+++ /dev/null\n")
    assert "-a\n-b\n" in out


def test_diff_for_deleted_empty_file_still_shows_header() -> None:
    assert unified_diff("", "", "x.conf", deleted=True) == (
        "--- a/x.conf\n+++ /dev/null\n"
    )
