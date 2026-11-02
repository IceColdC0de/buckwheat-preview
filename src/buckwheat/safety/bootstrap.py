"""The bootstrap edit check.

The user's main config is writable only for buckwheat's marked include block.
``check_include_edit`` compares the old and new text of such a file and raises
unless they differ by exactly one inserted or removed marker block, so an
adapter bug cannot touch anything the user wrote.

One exception: alacritty may append its entry to an
existing one-line ``import = [...]`` array. Callers pass that entry as
``array_entry`` to allow exactly that edit (or its undo) and nothing else.
"""

from __future__ import annotations

import re

INCLUDE_BEGIN = "# >>> buckwheat (managed include) >>>"
INCLUDE_END = "# <<< buckwheat <<<"

# Text allowed around the markers but outside them: line breaks, indentation
# and the comment leaders of the file formats we edit (``//``, ``/* */``, and
# Lua's ``--``).
_OUTSIDE_OK = re.compile(r"(?:\s|//|/\*|\*/|--)*")
_LINE = re.compile(r"[^\n]*\n|[^\n]+")
_IMPORT = re.compile(r"^([ \t]*import[ \t]*=[ \t]*\[)([^\[\]#\r\n]*)(\][ \t]*\r?\n?)$")


class BootstrapEditError(Exception):
    """The edit to a user file is not a pure marker-block insertion or removal."""


def check_include_edit(old: str, new: str, *, array_entry: str | None = None) -> None:
    """Raise ``BootstrapEditError`` unless ``new`` differs from ``old`` only by
    one marker block (or, with ``array_entry``, by that one array entry)."""
    if old == new:
        return
    if array_entry is not None and _is_array_edit(old, new, array_entry):
        return
    small, big = (old, new) if len(old) < len(new) else (new, old)
    if INCLUDE_BEGIN in small or INCLUDE_END in small:
        raise BootstrapEditError("the file would keep a buckwheat marker")
    chunk = _inserted(small, big)
    if chunk is None:
        raise BootstrapEditError("the edit changes text outside the marker block")
    if chunk.count(INCLUDE_BEGIN) != 1 or chunk.count(INCLUDE_END) != 1:
        raise BootstrapEditError("the edit is not exactly one marker block")
    begin = chunk.index(INCLUDE_BEGIN)
    end = chunk.find(INCLUDE_END, begin)
    if end < 0:
        raise BootstrapEditError("the marker block is malformed")
    outside = chunk[:begin] + chunk[end + len(INCLUDE_END) :]
    if not _OUTSIDE_OK.fullmatch(outside):
        raise BootstrapEditError("the edit changes text outside the marker block")


def _inserted(small: str, big: str) -> str | None:
    """The text inserted into ``small`` to get ``big``, or None if not an insertion."""
    p = 0
    limit = len(small)
    while p < limit and small[p] == big[p]:
        p += 1
    s = 0
    while s < limit - p and small[-1 - s] == big[-1 - s]:
        s += 1
    if p + s != len(small):
        return None
    return big[p : len(big) - s]


def _is_array_edit(old: str, new: str, entry: str) -> bool:
    """True if one ``import = [...]`` line gained (or lost) ``entry`` at its end."""
    old_lines, new_lines = _LINE.findall(old), _LINE.findall(new)
    if len(old_lines) != len(new_lines):
        return False
    diffs = [
        i for i, (a, b) in enumerate(zip(old_lines, new_lines, strict=True)) if a != b
    ]
    if len(diffs) != 1:
        return False
    a, b = old_lines[diffs[0]], new_lines[diffs[0]]
    short, long = (a, b) if len(a) < len(b) else (b, a)
    ms, ml = _IMPORT.match(short), _IMPORT.match(long)
    if ms is None or ml is None or ms[1] != ml[1] or ms[3] != ml[3]:
        return False
    inner = ms[2]
    if inner.strip():
        return ml[2] == f"{inner}, {entry}"
    return ml[2] == f"{inner}{entry}"
