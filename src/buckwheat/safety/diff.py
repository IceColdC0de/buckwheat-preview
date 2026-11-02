"""Unified diff for the preview screen."""

from __future__ import annotations

import difflib


def _lines(text: str) -> list[str]:
    """Split into lines, marking a missing final newline like ``git diff``."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


def unified_diff(old: str, new: str, path: str, *, deleted: bool = False) -> str:
    """Return a unified diff of ``old`` -> ``new`` for ``path`` ("" if equal).

    With ``deleted`` the file is being removed: ``new`` is ignored, the target
    is ``/dev/null`` like ``git diff``, and the header is shown even for an
    empty file so the preview never hides a deletion.
    """
    if deleted:
        header = f"--- a/{path}\n+++ /dev/null\n"
        body = "".join(
            difflib.unified_diff(
                _lines(old), [], fromfile=f"a/{path}", tofile="/dev/null"
            )
        )
        return body or header
    return "".join(
        difflib.unified_diff(
            _lines(old),
            _lines(new),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
