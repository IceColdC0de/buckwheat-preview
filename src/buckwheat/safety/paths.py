"""Path allowlist check: the only gate before any write."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class PathNotAllowedError(Exception):
    """Raised when a path is not on the adapter's allowlist."""


def _resolve(path: str | Path) -> Path:
    """Resolve ``path``, raising ``OSError`` if it cannot be (e.g. a symlink loop).

    Newer Pythons return a partly resolved path on a loop instead of raising,
    so also fail if any part of the result is still a symlink.
    """
    resolved = Path(path).expanduser().resolve()
    for part in (resolved, *resolved.parents):
        if part.is_symlink():
            raise OSError(f"symlink loop: {part}")
    return resolved


def guard_path(path: str | Path, allowlist: Iterable[str | Path]) -> Path:
    """Return the resolved real path if it is on the allowlist, else raise.

    Both the path and every allowlist entry are resolved first (``..`` and
    symlinks followed), then compared exactly. A path that only looks allowed
    (``allowed/../../etc/passwd``) or that reaches a forbidden file through a
    symlink resolves to something not on the list and is rejected.
    """
    if "\0" in str(path):
        raise PathNotAllowedError("path contains a NUL byte")
    try:
        resolved = _resolve(path)
        allowed = {_resolve(a) for a in allowlist}
    except (OSError, RuntimeError) as exc:  # e.g. symlink loop
        raise PathNotAllowedError(f"cannot resolve {path}: {exc}") from exc
    if resolved not in allowed:
        raise PathNotAllowedError(f"path is not on the allowlist: {path}")
    return resolved
