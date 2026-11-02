"""Atomic file writes behind the path allowlist."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path

from buckwheat.safety.paths import guard_path

DEFAULT_MODE = 0o644


def atomic_write(path: str | Path, text: str, allowlist: Iterable[str | Path]) -> Path:
    """Write ``text`` to ``path`` so readers see the old or new file, never half.

    Steps: guard the path, write a temp file in the same directory, fsync it,
    set the mode (copied from the existing file, else 0644), rename over the
    target, fsync the directory. If anything fails the temp file is removed
    and the original is untouched. Returns the real path written.
    """
    if os.geteuid() == 0:
        raise PermissionError("refusing to write as root")
    target = guard_path(path, allowlist)

    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = DEFAULT_MODE

    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise

    _fsync_dir(target.parent)
    return target


def safe_delete(path: str | Path, allowlist: Iterable[str | Path]) -> Path | None:
    """Delete ``path`` if it is on the allowlist; the counterpart of atomic_write.

    The path is checked with ``guard_path`` exactly like a write, so an
    allowlisted symlink is followed and its real target is removed (the same
    file atomic_write would have replaced, and the path snapshots record).
    Only regular files are deleted. A missing file is not an error. Returns
    the real path removed, or None if there was nothing to remove.
    """
    if os.geteuid() == 0:
        raise PermissionError("refusing to delete as root")
    target = guard_path(path, allowlist)
    try:
        st = target.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise IsADirectoryError(f"refusing to delete a non-regular file: {target}")
    try:
        target.unlink()
    except FileNotFoundError:
        return None
    _fsync_dir(target.parent)
    return target


def _fsync_dir(directory: Path) -> None:
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
