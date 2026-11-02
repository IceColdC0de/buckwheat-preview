"""kitty adapter.

Managed file ``buckwheat.conf`` is regenerated in full from the managed state
overlaid with the plan's ops. ``kitty.conf`` only ever gets (or loses) the
marked include block. The adapter returns text; the session writes it through
``safety.atomic_write``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

from buckwheat.adapters.base import (
    INCLUDE_BEGIN,
    INCLUDE_END,
    MANAGED_HEADER,
    BootstrapPlan,
    BootstrapStatus,
    CommandError,
    CommandResult,
    CommandRunner,
    Detection,
    KeySpec,
    ReloadResult,
    RenderResult,
    SubprocessRunner,
    TargetState,
    is_managed_file,
    load_managed_state,
    managed_state_path,
    merge_state,
    register,
)
from buckwheat.config import _xdg_dir
from buckwheat.plan.context import Problem
from buckwheat.plan.models import FontOp, Op, PaletteOp, Scalar, SetOp
from buckwheat.plan.palettes import PaletteError, PaletteVariant, load_palette

INCLUDE_LINE = "include buckwheat.conf"
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_VERSION_RE = re.compile(r"^kitty\s+(\S+)")


def _default_dir() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", ".config") / "kitty"


_NON_COLOUR = (
    "font_family",
    "font_size",
    "background_opacity",
    "window_padding_width",
    "cursor_shape",
)
KEYS: dict[str, KeySpec] = {
    "font_family": KeySpec(str),
    "font_size": KeySpec(float, min=6, max=48),
    "background_opacity": KeySpec(float, min=0, max=1),
    "window_padding_width": KeySpec(float, min=0, max=100),
    "cursor_shape": KeySpec(str, enum=("block", "beam", "underline")),
    **{
        name: KeySpec(str)
        for name in (
            "foreground",
            "background",
            "cursor",
            "selection_background",
            "selection_foreground",
            *(f"color{i}" for i in range(16)),
        )
    },
}


def _palette_values(pv: PaletteVariant) -> dict[str, Scalar]:
    c = pv.colors
    values: dict[str, Scalar] = {
        "foreground": c.fg,
        "background": c.bg,
        "cursor": c.cursor,
        "selection_background": c.selection_bg,
        "selection_foreground": c.selection_fg,
    }
    ansi = pv.ansi
    order = [
        ansi.black, ansi.red, ansi.green, ansi.yellow,
        ansi.blue, ansi.magenta, ansi.cyan, ansi.white,
        ansi.bright_black, ansi.bright_red, ansi.bright_green, ansi.bright_yellow,
        ansi.bright_blue, ansi.bright_magenta, ansi.bright_cyan, ansi.bright_white,
    ]  # fmt: skip
    for i, colour in enumerate(order):
        values[f"color{i}"] = colour
    return values


def _render_value(value: Scalar) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _block_span(lines: list[str]) -> tuple[int, int] | None:
    """Index range [begin, end] of the one include block, None if absent."""
    begins = [i for i, ln in enumerate(lines) if ln.strip() == INCLUDE_BEGIN]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == INCLUDE_END]
    if len(begins) == 1 and len(ends) == 1 and begins[0] < ends[0]:
        return begins[0], ends[0]
    return None


@register("kitty")
class KittyAdapter:
    name: ClassVar[str] = "kitty"
    # Only our own file. The user's kitty.conf is the bootstrap file (bootstrap_plan),
    # writable only for the marked include block; callers add it for install/uninstall.
    managed_files: ClassVar[list[Path]] = [_default_dir() / "buckwheat.conf"]

    def __init__(
        self,
        runner: CommandRunner | None = None,
        config_dir: Path | None = None,
        state_path: Path | None = None,
    ) -> None:
        self._runner = runner or SubprocessRunner()
        self._dir = config_dir or _default_dir()
        self._state_path = state_path or managed_state_path()

    @property
    def user_file(self) -> Path:
        return self._dir / "kitty.conf"

    @property
    def managed_file(self) -> Path:
        return self._dir / "buckwheat.conf"

    def bootstrap_plan(self) -> BootstrapPlan:
        return BootstrapPlan(file=self.user_file, line=INCLUDE_LINE)

    def bootstrap_files(self) -> list[Path]:
        return [self.user_file]

    def _read_user(self) -> str:
        try:
            # Bytes, not read_text: universal newlines would turn CRLF into LF.
            return self.user_file.read_bytes().decode("utf-8")
        except FileNotFoundError:
            return ""

    # --- Adapter protocol ---

    def detect(self) -> Detection:
        try:
            res = self._runner.run(["kitty", "--version"])
        except CommandError:
            return Detection(installed=False)
        if res.returncode != 0:
            return Detection(installed=False)
        m = _VERSION_RE.match(res.stdout)
        running = self._pgrep().returncode == 0
        return Detection(
            installed=True,
            version=m.group(1) if m else None,
            config_dir=self._dir,
            running=running,
        )

    def _pgrep(self) -> CommandResult:
        try:
            return self._runner.run(["pgrep", "-x", "-U", str(os.getuid()), "kitty"])
        except CommandError:
            return CommandResult(returncode=2)

    def read_state(self) -> TargetState:
        stored = load_managed_state(self._state_path).get("kitty", {})
        values = {k: v for k, v in stored.items() if k in KEYS}
        lines = self._read_user().splitlines()
        span = _block_span(lines)
        status: BootstrapStatus
        shadowed: list[str] = []
        if span is None:
            has_marker = any(ln.strip() in (INCLUDE_BEGIN, INCLUDE_END) for ln in lines)
            status = "weird" if has_marker else "none"
        else:
            status = "bootstrapped"
            # Later wins in kitty: user lines after the include override us.
            for ln in lines[span[1] + 1 :]:
                parts = ln.split(None, 1)
                if parts and parts[0] in KEYS and parts[0] not in shadowed:
                    shadowed.append(parts[0])
            if shadowed:
                status = "user-shadowed"
        return TargetState(
            values=values, bootstrap=status, shadowed_keys=tuple(shadowed)
        )

    def validate(self, op: Op) -> list[Problem]:
        if isinstance(op, SetOp):
            if op.target != "kitty":
                return [_problem("wrong_target", "op is not for kitty")]
            spec = KEYS.get(op.key)
            if spec is None:
                return [_problem("unknown_key", f"{op.key}: not a kitty key")]
            problems = spec.check(op.key, op.value)
            if (
                not problems
                and op.key not in _NON_COLOUR
                and not _HEX_RE.match(str(op.value))
            ):
                problems = [_problem("bad_color", f"{op.key}: expected #rrggbb")]
            if not problems and isinstance(op.value, str) and not _one_line(op.value):
                problems = [_problem("bad_value", f"{op.key}: must be one line")]
            return problems
        if isinstance(op, FontOp):
            if op.target != "kitty":
                return [_problem("wrong_target", "op is not for kitty")]
            if op.role != "terminal":
                return [_problem("bad_role", "kitty only has a terminal font")]
            if not _one_line(op.family):
                return [_problem("bad_value", "font family must be one line")]
            return []
        if isinstance(op, PaletteOp):
            if "kitty" not in op.targets:
                return [_problem("wrong_target", "op is not for kitty")]
            try:
                load_palette(op.palette, op.variant)
            except PaletteError as exc:
                return [_problem("bad_palette", str(exc))]
            return []
        return [_problem("unsupported_op", "kitty does not handle this op")]

    def allowed_keys(self) -> list[str]:
        return sorted(KEYS)

    def state_updates(
        self, ops: Sequence[Op], state: TargetState
    ) -> Mapping[str, Scalar]:
        """Registry key -> value that ``ops`` write (set, font and palette ops)."""
        updates: dict[str, Scalar] = {}
        for op in ops:
            if self.validate(op):
                continue  # callers validate first; never emit a bad value
            updates.update(_op_values(op))
        return updates

    def render(self, ops: list[Op], state: TargetState) -> RenderResult:
        values = merge_state(dict(state.values), self.state_updates(ops, state))
        return RenderResult(files={self.managed_file: _render_file(values)})

    def reload(self) -> ReloadResult:
        fallback = "press ctrl+shift+f5 in kitty to reload"
        try:
            res = self._runner.run(
                ["pkill", "-USR1", "-x", "-U", str(os.getuid()), "kitty"]
            )
        except CommandError as exc:
            return ReloadResult(False, f"{exc}; {fallback}")
        if res.returncode == 0:
            return ReloadResult(True, "sent SIGUSR1 to kitty")
        if res.returncode == 1:
            return ReloadResult(True, "kitty is not running; applies on next start")
        return ReloadResult(False, f"pkill failed ({res.returncode}); {fallback}")

    def install(self, plan: BootstrapPlan) -> RenderResult:
        if plan.file != self.user_file or plan.line != INCLUDE_LINE:
            raise ValueError("bootstrap plan does not match the kitty include")
        text = self._read_user()
        if _block_span(text.splitlines()) is not None:
            return RenderResult(files={self.user_file: text})
        nl = "\r\n" if "\r\n" in text else "\n"
        block = nl.join([INCLUDE_BEGIN, INCLUDE_LINE, INCLUDE_END])
        # The block ends with a newline iff the file did, so uninstall is exact.
        if text == "" or text.endswith("\n"):
            text += block + nl
        else:
            text += nl + block
        stored = load_managed_state(self._state_path).get("kitty", {})
        return RenderResult(
            files={self.user_file: text, self.managed_file: _render_file(stored)}
        )

    def uninstall(self) -> RenderResult:
        """Strip the include block and delete our managed file (if it is ours).

        A bare ``include buckwheat.conf`` outside the block is not ours to edit,
        so it is reported as a leftover for the user to remove by hand.
        """
        lines = self._read_user().splitlines(keepends=True)
        span = _block_span([ln.rstrip("\r\n") for ln in lines])
        if span is not None:
            at_end = span[1] == len(lines) - 1
            unterminated = not lines[span[1]].endswith("\n")
            del lines[span[0] : span[1] + 1]
            if at_end and unterminated and lines:
                # install added a line break before the block: take it back.
                lines[-1] = lines[-1].rstrip("\r\n")
        deletes = (
            frozenset({self.managed_file})
            if is_managed_file(self.managed_file)
            else frozenset()
        )
        leftovers: tuple[str, ...] = ()
        if any(ln.strip() == INCLUDE_LINE for ln in lines):
            leftovers = (
                f"{self.user_file.name} still has the line `{INCLUDE_LINE}` outside "
                "buckwheat's marked block. Buckwheat only edits its own block, so "
                "remove that line by hand.",
            )
        return RenderResult(
            files={self.user_file: "".join(lines)},
            deletes=deletes,
            leftovers=leftovers,
        )


def _problem(code: str, message: str) -> Problem:
    return Problem(code=code, message=message)


def _one_line(text: str) -> bool:
    return not any(c in text for c in "\r\n\0")


def _op_values(op: Op) -> dict[str, Scalar]:
    if isinstance(op, SetOp):
        return {op.key: op.value}
    if isinstance(op, FontOp):
        out: dict[str, Scalar] = {"font_family": op.family}
        if op.size is not None:
            out["font_size"] = op.size
        return out
    if isinstance(op, PaletteOp):
        return _palette_values(load_palette(op.palette, op.variant))
    return {}


def _render_file(values: Mapping[str, Scalar]) -> str:
    out = [MANAGED_HEADER.replace("<target>", "kitty"), ""]
    for key in KEYS:  # registry order: deterministic output
        if key in values:
            out.append(f"{key} {_render_value(values[key])}")
    return "\n".join(out) + "\n"
