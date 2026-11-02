"""Safety layer: backups, diff previews, rollback."""

from buckwheat.safety.atomic import atomic_write, safe_delete
from buckwheat.safety.diff import unified_diff
from buckwheat.safety.paths import PathNotAllowedError, guard_path

__all__ = [
    "PathNotAllowedError",
    "atomic_write",
    "guard_path",
    "safe_delete",
    "unified_diff",
]
