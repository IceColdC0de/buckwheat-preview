"""Common value-layer validation.

``validate_common`` checks string, font, colour and wallpaper-path rules that
do not depend on a particular adapter. It never raises on bad plan content;
it returns one ``Problem`` per violation. The only I/O is stat/realpath on
the wallpaper path.
"""

import math
import re
from collections.abc import Sequence
from pathlib import Path, PurePath

from buckwheat.plan.context import Problem
from buckwheat.plan.models import ChangePlan, FontOp, SetOp, WallpaperOp

FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9 ._+\-]{1,80}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# What any colour value may look like before the adapter checks it: one or more
# #rrggbb / #rrggbbaa colours and an optional angle (Hyprland gradients:
# "#88c0d0 #b48ead 45deg"). Adapters accept only what their app supports.
COLOR_VALUE_RE = re.compile(
    r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?"
    r"(?: #[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?)*(?: -?\d{1,3}deg)?$"
)
WALLPAPER_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MAX_WALLPAPER_BYTES = 100 * 1024 * 1024
MAX_STRING_VALUE = 200
# Backtick, "$(", "${", ";", "|", "&", "<", ">", backslash.
_SHELL_META_RE = re.compile(r"[`;|&<>\\]|\$[({]")
# Key segments that mean "this key holds a colour".
_COLOR_KEY_RE = re.compile(
    r"(^|\.)col(\.|$)|(^|\.|_)(colou?r\d*|foreground|background|fg|bg)(\.|$)"
)


DEFAULT_WALLPAPER_ROOTS = (
    "~/Pictures",
    "~/.local/share/backgrounds",
    "/usr/share/backgrounds",
)


def _has_control_chars(text: str) -> bool:
    # C0, DEL and C1 controls, plus Unicode line/paragraph separators.
    return any(
        ord(c) < 32 or 0x7F <= ord(c) <= 0x9F or c in "\u2028\u2029" for c in text
    )


def _check_text(
    text: str, what: str, index: int, *, allow_shell_meta: bool = False
) -> list[Problem]:
    problems = []
    if _has_control_chars(text):
        problems.append(
            Problem(
                op_index=index,
                code="control-chars",
                message=f"{what} contains control characters",
            )
        )
    if not allow_shell_meta and _SHELL_META_RE.search(text):
        problems.append(
            Problem(
                op_index=index,
                code="bad-chars",
                message=f"{what} contains forbidden characters",
            )
        )
    return problems


def _check_set(op: SetOp, index: int) -> list[Problem]:
    problems = _check_text(op.key, "key", index)
    value = op.value
    if isinstance(value, float) and not math.isfinite(value):
        problems.append(
            Problem(op_index=index, code="bad-number", message="value must be finite")
        )
    if isinstance(value, str):
        if len(value) > MAX_STRING_VALUE:
            problems.append(
                Problem(
                    op_index=index,
                    code="too-long",
                    message=f"value is longer than {MAX_STRING_VALUE} characters",
                )
            )
        problems += _check_text(value, "value", index)
        if (
            _COLOR_KEY_RE.search(op.key.lower()) or value.startswith("#")
        ) and not COLOR_VALUE_RE.match(value):
            problems.append(
                Problem(
                    op_index=index,
                    code="bad-color",
                    message="colour must look like #rrggbb",
                )
            )
    elif _COLOR_KEY_RE.search(op.key.lower()):
        problems.append(
            Problem(
                op_index=index,
                code="bad-color",
                message="colour must be a #rrggbb string",
            )
        )
    return problems


def _check_font(op: FontOp, index: int) -> list[Problem]:
    problems = []
    # fullmatch-style anchors would let a trailing "\n" through "$", so test explicitly.
    if "\n" in op.family or not FONT_FAMILY_RE.match(op.family):
        problems.append(
            Problem(
                op_index=index,
                code="bad-font",
                message="font family may only use letters, digits, space and . _ + -",
            )
        )
    return problems


def _check_wallpaper(
    op: WallpaperOp, index: int, home: Path, roots: Sequence[str | Path]
) -> list[Problem]:
    def bad(code: str, message: str) -> list[Problem]:
        return [Problem(op_index=index, code=code, message=message)]

    raw = op.path
    if not raw:
        return bad("bad-path", "wallpaper path is empty")
    if _has_control_chars(raw):
        return bad("control-chars", "wallpaper path contains control characters")
    if raw == "~" or raw.startswith("~/"):
        raw = str(home) + raw[1:]
    elif not raw.startswith("/"):
        return bad("bad-path", "wallpaper path must be absolute or start with ~/")
    if ".." in PurePath(raw).parts:
        return bad("path-traversal", "wallpaper path may not contain '..'")

    try:
        resolved = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError):
        return bad("bad-path", "wallpaper file does not exist or cannot be resolved")

    allowed = []
    for root in roots:
        text = str(root)
        p = home / text[2:] if text.startswith("~/") else Path(text)
        allowed.append(p.resolve())
    if not any(resolved.is_relative_to(a) for a in allowed):
        return bad("path-not-allowed", "wallpaper is not under an allowed directory")
    if not resolved.is_file():
        return bad("not-a-file", "wallpaper is not a regular file")
    if resolved.suffix.lower() not in WALLPAPER_EXTENSIONS:
        return bad("bad-extension", "wallpaper must be .png, .jpg, .jpeg or .webp")
    if resolved.stat().st_size > MAX_WALLPAPER_BYTES:
        return bad("too-large", "wallpaper is larger than 100 MB")
    return []


def validate_common(
    plan: ChangePlan,
    *,
    home: Path | None = None,
    extra_wallpaper_dirs: Sequence[str | Path] = (),
) -> list[Problem]:
    """Return every common-layer problem in ``plan`` (empty list means OK).

    ``home`` and ``extra_wallpaper_dirs`` exist for tests and for
    ``paths.extra_wallpaper_dirs`` from the user's config.
    """
    home = home if home is not None else Path.home()
    roots = [*DEFAULT_WALLPAPER_ROOTS, *extra_wallpaper_dirs]
    problems: list[Problem] = []
    for p in _check_text(plan.summary, "summary", 0):
        problems.append(Problem(op_index=None, code=p.code, message=p.message))
    for i, op in enumerate(plan.ops):
        if isinstance(op, SetOp):
            problems += _check_set(op, i)
        elif isinstance(op, FontOp):
            problems += _check_font(op, i)
        elif isinstance(op, WallpaperOp):
            problems += _check_wallpaper(op, i, home, roots)
        # PaletteOp: names are fully constrained by the schema regexes.
    return problems
