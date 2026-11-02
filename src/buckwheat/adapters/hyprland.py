"""Hyprland adapter (design.md section 3.3).

Two config formats, chosen by detection (``uses_lua``):

- **Lua** (Hyprland 0.57+, or whenever ``hyprland.lua`` exists): the managed
  file is ``buckwheat.lua`` with one ``hl.config({...})`` call, and the user's
  ``hyprland.lua`` gets a marked block at its end holding only
  ``pcall(require, "buckwheat")``. The block goes last so buckwheat's values
  win over the user's earlier modules. ``pcall`` keeps a missing
  ``buckwheat.lua`` from being a fatal config error, while errors inside it are
  still reported. Before anything is written, the proposed files are checked
  with ``Hyprland --verify-config`` on a temporary copy of the config folder,
  when the ``Hyprland`` binary is available.
- **Classic** (hyprlang, before 0.57): the managed file is ``buckwheat.conf``
  and ``hyprland.conf`` gets a marked ``source = ...`` block.

The managed file is regenerated in full from the managed state (old values
overlaid with the ops). The adapter only returns text; the session writes it
through ``safety.atomic_write``. The only user files we ever touch are
``hyprland.lua`` / ``hyprland.conf``, and only for the marked include block.
A symlinked ``hyprland.lua`` (dotfiles) is edited in place at its target; the
link itself is kept.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from buckwheat.adapters.base import (
    INCLUDE_BEGIN,
    INCLUDE_END,
    MANAGED_HEADER,
    BootstrapPlan,
    BootstrapStatus,
    CommandError,
    CommandRunner,
    Detection,
    KeySpec,
    ReloadResult,
    RenderResult,
    SubprocessRunner,
    TargetState,
    is_managed_file,
    load_managed_state,
    merge_state,
    register,
)
from buckwheat.adapters.hyprland_keys import (
    KeyInfo,
    Kind,
    allowed_keys,
    describe,
    raw_key_types,
)
from buckwheat.config import _xdg_dir
from buckwheat.plan.context import Problem
from buckwheat.plan.models import FontOp, Op, PaletteOp, Scalar, SetOp, TargetName
from buckwheat.plan.palettes import PaletteError, load_palette

TARGET: TargetName = "hyprland"
MAIN_CONFIG = "hyprland.conf"
LUA_CONFIG = "hyprland.lua"
MANAGED_CONFIG = "buckwheat.conf"
LUA_MANAGED_CONFIG = "buckwheat.lua"
# First Hyprland release that reads only hyprland.lua (no hyprlang).
LUA_ONLY_SINCE = (0, 57)
LUA_KIND = "Lua config (hyprland.lua)"
CLASSIC_KIND = "classic config (hyprland.conf)"
# The one line inside the Lua block. ``pcall``: a missing buckwheat.lua is not
# fatal (plain ``require`` would be), but errors inside it are still reported.
LUA_INCLUDE_LINE = 'pcall(require, "buckwheat")'
LUA_BEGIN = f"-- {INCLUDE_BEGIN}"
LUA_END = f"-- {INCLUDE_END}"
LUA_MANAGED_HEADER = "-- " + MANAGED_HEADER.lstrip("# ")
NO_LUA_CONFIG = (
    "Hyprland 0.57+ reads hyprland.lua, which does not exist yet; buckwheat "
    "does not create your main config. Create it (Hyprland makes one on first "
    "start), then set Hyprland up again"
)
HYPRLAND_BIN = "Hyprland"
VERIFY_TIMEOUT = 30.0
# Files bigger than this (wallpapers and the like) are left out of the copy
# that ``Hyprland --verify-config`` checks.
_COPY_MAX_BYTES = 4 * 1024 * 1024

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_VERSION = re.compile(r"v?(\d+\.\d+\.\d+)")
_SOURCE_LINE = re.compile(r"^\s*source\s*=\s*(.*?)\s*$")
# A Lua line that loads our module: require("buckwheat"), require "buckwheat",
# pcall(require, "buckwheat"), ...
_LUA_REQUIRE = re.compile(r"""\brequire\b.*["']buckwheat["']""")
_RESULT_MARK = "Config parsing result:"


def _color(value: Scalar) -> str:
    """``#rrggbb`` -> ``rgba(rrggbbff)`` (only called on checked values)."""
    return f"rgba({str(value)[1:].lower()}ff)"


def _bool(value: Scalar) -> str:
    return "true" if value else "false"


def _num(value: Scalar) -> str:
    return repr(float(value)) if isinstance(value, float) else str(value)


@dataclass(frozen=True)
class _Key:
    path: tuple[str, ...]  # nested sections, outermost first
    name: str  # the name written inside the innermost section
    spec: KeySpec


def _k(dotted: str, name: str, spec: KeySpec) -> tuple[str, _Key]:
    """``dotted`` is the registry key; ``name`` is the final written name."""
    parts = dotted.split(".")
    depth = len(parts) - len(name.split("."))
    return dotted, _Key(tuple(parts[:depth]), name, spec)


_COLOR_SPEC = KeySpec(str, render=_color)
_KEYS: dict[str, _Key] = dict(
    [
        _k("general.gaps_in", "gaps_in", KeySpec(int, 0, 100, render=_num)),
        _k("general.gaps_out", "gaps_out", KeySpec(int, 0, 200, render=_num)),
        _k("general.border_size", "border_size", KeySpec(int, 0, 20, render=_num)),
        _k("general.col.active_border", "col.active_border", _COLOR_SPEC),
        _k("general.col.inactive_border", "col.inactive_border", _COLOR_SPEC),
        _k("decoration.rounding", "rounding", KeySpec(int, 0, 50, render=_num)),
        _k(
            "decoration.active_opacity",
            "active_opacity",
            KeySpec(float, 0.0, 1.0, render=_num),
        ),
        _k(
            "decoration.inactive_opacity",
            "inactive_opacity",
            KeySpec(float, 0.0, 1.0, render=_num),
        ),
        _k("decoration.blur.enabled", "enabled", KeySpec(bool, render=_bool)),
        _k("decoration.blur.size", "size", KeySpec(int, 1, 20, render=_num)),
        _k("animations.enabled", "enabled", KeySpec(bool, render=_bool)),
    ]
)

# Palette slot map: registry key <- palette colour slot.
_PALETTE_SLOTS = {
    "general.col.active_border": "accent",
    "general.col.inactive_border": "surface",
}
# Lua (0.57+) only: more of Hyprland follows the palette. Group borders and
# the group bar match the window borders; the group bar's titles use the
# palette's text colour; the shadow and the colour behind the wallpaper come
# from the background. A shadow must be darker than what is behind it, so it
# is the palette background darkened to a tinted near-black (see
# ``_shadow_color``), fainter for light palettes.
_LUA_PALETTE_SLOTS = {
    **_PALETTE_SLOTS,
    "group.col.border_active": "accent",
    "group.col.border_inactive": "surface",
    "group.col.border_locked_active": "warning",
    "group.col.border_locked_inactive": "surface",
    "group.groupbar.col.active": "accent",
    "group.groupbar.col.inactive": "surface",
    "group.groupbar.col.locked_active": "warning",
    "group.groupbar.col.locked_inactive": "surface",
    "group.groupbar.text_color": "fg",
    "group.groupbar.text_color_inactive": "fg_dim",
    "misc.background_color": "bg",
}
_SHADOW_KEY = "decoration.shadow.color"

# How the original keys are typed in Lua mode when the installed stub lacks
# them (so values saved by older buckwheat versions always render).
_LEGACY_KINDS: dict[str, Kind] = {
    "general.gaps_in": "gap",
    "general.gaps_out": "gap",
    "general.border_size": "int",
    "general.col.active_border": "gradient",
    "general.col.inactive_border": "gradient",
    "decoration.rounding": "int",
    "decoration.active_opacity": "float",
    "decoration.inactive_opacity": "float",
    "decoration.blur.enabled": "bool",
    "decoration.blur.size": "int",
    "animations.enabled": "bool",
}


def _shadow_color(bg: str, dark: bool) -> str:
    """``#rrggbbaa`` shadow: ``bg`` at 25% brightness, mostly opaque on dark
    palettes, half see-through on light ones."""
    channels = (int(bg[i : i + 2], 16) * 25 // 100 for i in (1, 3, 5))
    alpha = "ee" if dark else "88"
    return "#" + "".join(f"{c:02x}" for c in channels) + alpha


def _config_home() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", ".config") / "hypr"


@register("hyprland")
class HyprlandAdapter:
    name: ClassVar[str] = TARGET
    # Both formats' own files: uninstall cleans up either, whichever is in use.
    managed_files: ClassVar[list[Path]] = [
        _config_home() / MANAGED_CONFIG,
        _config_home() / LUA_MANAGED_CONFIG,
    ]

    def __init__(
        self,
        runner: CommandRunner | None = None,
        config_dir: Path | None = None,
        hyprland_bin: str | None = None,
        key_types: Mapping[str, str] | None = None,
    ) -> None:
        """``hyprland_bin`` is the ``Hyprland`` binary for ``--verify-config``.

        With no runner (the real default) it is looked up on PATH. Tests that
        pass a runner opt in by naming it; None means no checker, so only
        buckwheat's own key and range checks run.

        ``key_types`` (key -> stub type) is what Hyprland 0.57+ accepts; by
        default the installed stub file, else buckwheat's generated copy.
        """
        types = raw_key_types() if key_types is None else key_types
        lua_keys = {k: KeyInfo(k, kind) for k, kind in _LEGACY_KINDS.items()}
        lua_keys.update(allowed_keys(types))
        self.lua_keys: dict[str, KeyInfo] = lua_keys
        if runner is None and hyprland_bin is None:
            hyprland_bin = shutil.which(HYPRLAND_BIN)
        self.runner: CommandRunner = runner or SubprocessRunner()
        self.hyprland_bin = hyprland_bin
        self.config_dir = config_dir if config_dir is not None else _config_home()
        self.main_path = self.config_dir / MAIN_CONFIG
        self.lua_path = self.config_dir / LUA_CONFIG
        self.managed_path = self.config_dir / MANAGED_CONFIG
        self.lua_managed_path = self.config_dir / LUA_MANAGED_CONFIG

    # --- detection and state ------------------------------------------

    def _hyprctl_version(self) -> str | None:
        """The version ``hyprctl version`` reports, or None (not running, no match)."""
        try:
            res = self.runner.run(["hyprctl", "version"])
        except CommandError:
            return None
        if res.returncode != 0:
            return None
        m = _VERSION.search(res.stdout)
        return m.group(1) if m else ""

    def _lua_file_present(self) -> bool:
        # A dangling dotfiles symlink still means "this is the Lua config".
        return self.lua_path.is_symlink() or self.lua_path.exists()

    def uses_lua(self, version: str | None = None) -> bool:
        """True for the Lua config: ``hyprland.lua`` exists or Hyprland is 0.57+.

        ``version`` is what ``hyprctl version`` reported, if the caller has it.
        """
        if self._lua_file_present():
            return True
        if version is None:
            version = self._hyprctl_version()
        if version:
            major, minor, _ = (int(part) for part in version.split("."))
            return (major, minor) >= LUA_ONLY_SINCE
        return False

    def config_kind(self) -> str:
        return LUA_KIND if self.uses_lua() else CLASSIC_KIND

    def detect(self) -> Detection:
        version = self._hyprctl_version()
        running = version is not None
        installed = running or self.main_path.exists() or self._lua_file_present()
        lua = self.uses_lua(version)
        return Detection(
            installed=installed,
            version=version or None,
            config_dir=self.config_dir if self.config_dir.is_dir() else None,
            running=running,
            config_kind=LUA_KIND if lua else CLASSIC_KIND,
        )

    def unsupported_reason(self) -> str | None:
        """Why buckwheat cannot set this Hyprland up, or None if it can.

        Hyprland 0.57+ with no ``hyprland.lua``: buckwheat never creates the
        user's main config, so there is nothing to add the include to.
        """
        if self.uses_lua() and not self._lua_file_present():
            return NO_LUA_CONFIG
        return None

    def user_config_path(self) -> Path:
        """The user's main config for the format in use."""
        return self.lua_path if self.uses_lua() else self.main_path

    def _known(self) -> Mapping[str, object]:
        """The keys the config format in use can set."""
        return self.lua_keys if self.uses_lua() else _KEYS

    def read_state(self) -> TargetState:
        stored = load_managed_state().get(TARGET, {})
        known = self._known()
        values = {k: v for k, v in stored.items() if k in known}
        return TargetState(values=values, bootstrap=self._bootstrap_status())

    @staticmethod
    def _read(path: Path) -> str:
        # Bytes, not read_text: universal newlines would turn CRLF into LF.
        return path.read_bytes().decode("utf-8")

    def _read_main(self) -> str:
        return self._read(self.main_path)

    def _bootstrap_status(self) -> BootstrapStatus:
        lua = self.uses_lua()
        path = self.lua_path if lua else self.main_path
        begin, end = (LUA_BEGIN, LUA_END) if lua else (INCLUDE_BEGIN, INCLUDE_END)
        try:
            text = self._read(path)
        except FileNotFoundError:
            return "none"
        except (OSError, UnicodeDecodeError):
            return "weird"
        begins, ends = text.count(INCLUDE_BEGIN), text.count(INCLUDE_END)
        if begins == 0 and ends == 0:
            return "none"
        if begins == 1 and ends == 1 and _find_block(text, begin, end) is not None:
            return "bootstrapped"
        return "weird"

    # --- validation and rendering -------------------------------------

    def validate(self, op: Op) -> list[Problem]:
        if isinstance(op, SetOp):
            if op.target != TARGET:
                return []
            if self.uses_lua():
                info = self.lua_keys.get(op.key)
                if info is None:
                    return [
                        Problem(code="unknown_key", message=f"unknown key: {op.key}")
                    ]
                problem = info.check(op.value)
                if problem is None:
                    return []
                return [Problem(code="bad_value", message=problem)]
            entry = _KEYS.get(op.key)
            if entry is None:
                return [Problem(code="unknown_key", message=f"unknown key: {op.key}")]
            problems = entry.spec.check(op.key, op.value)
            if (
                not problems
                and entry.spec.render is _color
                and not _HEX.match(str(op.value))
            ):
                problems = [Problem(code="bad_color", message=f"{op.key}: use #rrggbb")]
            return problems
        if isinstance(op, PaletteOp):
            if TARGET not in op.targets:
                return []
            try:
                load_palette(op.palette, op.variant)
            except (PaletteError, KeyError, ValueError) as exc:
                return [Problem(code="bad_palette", message=str(exc)[:200])]
            return []
        if isinstance(op, FontOp) and op.target == TARGET:
            return [
                Problem(code="unsupported", message="hyprland has no font settings")
            ]
        return []  # wallpaper ops belong to the wallpaper adapter

    def allowed_keys(self) -> list[str]:
        return sorted(self._known())

    def describe_keys(self) -> str | None:
        """Compact typed key list for the planner (Lua only; classic is short)."""
        return describe(self.lua_keys) if self.uses_lua() else None

    def state_updates(
        self, ops: Sequence[Op], state: TargetState
    ) -> Mapping[str, Scalar]:
        """Registry key -> value that ``ops`` write, including palette colours."""
        updates: dict[str, Scalar] = {}
        lua = self.uses_lua()
        for op in ops:
            problems = self.validate(op)
            if problems:
                raise ValueError(f"invalid op for hyprland: {problems[0].message}")
            if isinstance(op, SetOp) and op.target == TARGET:
                updates[op.key] = op.value
            elif isinstance(op, PaletteOp) and TARGET in op.targets:
                palette = load_palette(op.palette, op.variant)
                colors = palette.colors
                slots = _LUA_PALETTE_SLOTS if lua else _PALETTE_SLOTS
                for key, slot in slots.items():
                    if key in self.lua_keys or not lua:
                        updates[key] = str(getattr(colors, slot)).lower()
                if lua and _SHADOW_KEY in self.lua_keys:
                    updates[_SHADOW_KEY] = _shadow_color(colors.bg, palette.dark)
        return updates

    def render(self, ops: list[Op], state: TargetState) -> RenderResult:
        """New managed file; for Lua, checked by ``Hyprland --verify-config`` first.

        Raises ValueError with the checker's message if Hyprland rejects it.
        """
        updates = self.state_updates(ops, state)
        lua = self.uses_lua()
        known_keys = self.lua_keys if lua else _KEYS
        known = {k: v for k, v in state.values.items() if k in known_keys}
        merged = merge_state(known, updates)
        if not lua:
            return RenderResult({self.managed_path: _render_text(merged)})
        files = {self.lua_managed_path: _render_lua(merged, self.lua_keys)}
        self._verify(files)
        return RenderResult(files)

    def _verify(self, proposed: Mapping[Path, str]) -> None:
        """Run ``Hyprland --verify-config`` on a copy of the config with ``proposed``.

        The whole config folder is copied (symlinks followed, big files left
        out) to a private temp folder, the proposed files are put in, and the
        copy's ``hyprland.lua`` is checked. If it has no buckwheat block yet,
        one is added to the copy so ``buckwheat.lua`` is checked too.
        Raises ValueError on any reported error. Does nothing when the binary
        is not available (buckwheat's own checks in ``validate`` still ran).
        """
        if self.hyprland_bin is None:
            return
        with tempfile.TemporaryDirectory(prefix="buckwheat-hypr-") as tmp:
            root = Path(tmp) / "hypr"
            runtime = Path(tmp) / "runtime"
            runtime.mkdir(mode=0o700)
            try:
                if self.config_dir.is_dir():
                    _copy_config(self.config_dir, root)
                else:
                    root.mkdir()
                for path, text in proposed.items():
                    target = root / path.relative_to(self.config_dir)
                    target.write_bytes(text.encode("utf-8"))
                main = root / LUA_CONFIG
                text = self._read(main) if main.is_file() else ""
                if LUA_BEGIN not in text:
                    main.write_bytes(_append_block(text, _lua_block).encode())
            except (OSError, ValueError, UnicodeDecodeError, shutil.Error) as exc:
                raise ValueError(
                    f"could not prepare the Hyprland config check: {exc}"
                ) from exc
            argv = [
                "env",
                f"XDG_RUNTIME_DIR={runtime}",
                self.hyprland_bin,
                "--verify-config",
                "-c",
                str(main),
            ]
            try:
                res = self.runner.run(argv, timeout=VERIFY_TIMEOUT)
            except CommandError:
                return  # cannot run the checker: buckwheat's own checks stand
            if res.returncode == 127:
                return  # env could not find the binary (removed since lookup)
            if res.returncode != 0:
                message = _verify_message(res.stdout + "\n" + res.stderr)
                message = message.replace(str(root), str(self.config_dir))
                raise ValueError(f"Hyprland --verify-config: {message}"[:400])

    # --- reload ---------------------------------------------------------

    def reload(self) -> ReloadResult:
        # Lua: config-only, so Hyprland does not set the monitors up again.
        argv = ["hyprctl", "reload"]
        if self.uses_lua():
            argv.append("config-only")
        try:
            res = self.runner.run(argv)
            if res.returncode != 0:
                return ReloadResult(False, f"hyprctl reload failed: {_tail(res)}")
            errs = self.runner.run(["hyprctl", "configerrors"])
        except CommandError as exc:
            return ReloadResult(False, str(exc))
        if errs.returncode != 0:
            return ReloadResult(False, f"hyprctl configerrors failed: {_tail(errs)}")
        text = errs.stdout.strip()
        if text and text.lower() != "no errors":
            return ReloadResult(False, f"Hyprland config errors: {text}")
        return ReloadResult(True)

    # --- bootstrap ------------------------------------------------------

    def include_line(self) -> str:
        if self.uses_lua():
            return LUA_INCLUDE_LINE
        try:
            shown = "~/" + self.managed_path.relative_to(Path.home()).as_posix()
        except ValueError:
            shown = str(self.managed_path)
        return f"source = {shown}"

    def bootstrap_plan(self) -> BootstrapPlan:
        return BootstrapPlan(file=self.user_config_path(), line=self.include_line())

    def bootstrap_files(self) -> list[Path]:
        return [self.main_path, self.lua_path]

    def install(self, plan: BootstrapPlan) -> RenderResult:
        if plan.file != self.user_config_path() or plan.line != self.include_line():
            raise ValueError("bootstrap plan does not match the hyprland include")
        reason = self.unsupported_reason()
        if reason is not None:
            raise ValueError(reason)  # before anything is read or written
        stored = load_managed_state().get(TARGET, {})
        lua = self.uses_lua()
        known_keys = self.lua_keys if lua else _KEYS
        known = {k: v for k, v in stored.items() if k in known_keys}
        if lua:
            # Read through a symlink: the session writes the link's target.
            try:
                text = self._read(self.lua_path)
            except FileNotFoundError:
                raise ValueError(
                    "hyprland.lua is a symlink to a file that does not exist"
                ) from None
            if INCLUDE_BEGIN in text or INCLUDE_END in text:
                raise ValueError("hyprland.lua already has a buckwheat block")
            files = {
                self.lua_path: _append_block(text, _lua_block),
                self.lua_managed_path: _render_lua(known, self.lua_keys),
            }
            self._verify(files)
            return RenderResult(files)
        try:
            text = self._read_main()
        except FileNotFoundError:
            text = ""
        if INCLUDE_BEGIN in text or INCLUDE_END in text:
            raise ValueError("hyprland.conf already has a buckwheat block")
        return RenderResult(
            {
                self.main_path: _append_block(
                    text, lambda nl: _classic_block(plan.line, nl)
                ),
                self.managed_path: _render_text(known),
            }
        )

    def uninstall(self) -> RenderResult:
        """Strip buckwheat's block from both formats and delete our own files.

        A managed file is only deleted if it carries buckwheat's header. A line
        that still loads our file after the block is gone (``source = ...`` in
        hyprland.conf, ``require``/``pcall`` of "buckwheat" in hyprland.lua) is
        reported as a leftover, whether or not a block was found.
        """
        deletes = frozenset(
            p
            for p in (self.managed_path, self.lua_managed_path)
            if is_managed_file(p)
        )
        files: dict[Path, str] = {}
        leftovers: list[str] = []
        for path, begin, end, strays in (
            (self.main_path, INCLUDE_BEGIN, INCLUDE_END, self._stray_sources),
            (self.lua_path, LUA_BEGIN, LUA_END, self._stray_requires),
        ):
            try:
                text = self._read(path)
            except FileNotFoundError:
                continue  # nothing to strip, and no line can point anywhere
            stripped = _strip_block(text, begin, end)
            if stripped is not None:
                text = stripped
                files[path] = text
            leftovers.extend(strays(text))
        return RenderResult(files, deletes, leftovers=tuple(leftovers))

    def _stray_sources(self, text: str) -> list[str]:
        """One leftover message per ``source`` line in ``text`` that names our file."""
        out: list[str] = []
        for line in text.splitlines():
            match = _SOURCE_LINE.match(line)
            if match is None:
                continue
            if self.config_dir / Path(match.group(1)).expanduser() == self.managed_path:
                out.append(
                    f"{self.main_path.name} still has the line `{line.strip()}`, "
                    f"which points at {self.managed_path.name}. Buckwheat only edits "
                    "its own marked block, so remove that line by hand."
                )
        return out

    def _stray_requires(self, text: str) -> list[str]:
        """One leftover message per Lua line in ``text`` that loads "buckwheat"."""
        out: list[str] = []
        for line in text.splitlines():
            code = line.strip()
            if code.startswith("--") or not _LUA_REQUIRE.search(code):
                continue
            out.append(
                f"{self.lua_path.name} still has the line `{code}`, which loads "
                f"{self.lua_managed_path.name}. Buckwheat only edits its own marked "
                "block, so remove that line by hand."
            )
        return out


def _find_block(text: str, begin: str, end_mark: str) -> tuple[int, int] | None:
    """Span of the marker block including its trailing newline, if any.

    The begin marker must start a line.
    """
    start = text.find(begin)
    if start < 0 or (start > 0 and text[start - 1] != "\n"):
        return None
    end = text.find(end_mark, start)
    if end < 0:
        return None
    end += len(end_mark)
    if text.startswith("\r\n", end):
        end += 2
    elif text.startswith("\n", end):
        end += 1
    return start, end


def _strip_block(text: str, begin: str, end_mark: str) -> str | None:
    """``text`` without the block (exactly undoing ``_append_block``), or None."""
    found = _find_block(text, begin, end_mark)
    if found is None:
        return None
    start, end = found
    if end == len(text) and not text.endswith("\n") and start > 0:
        # Block was appended to a file with no final newline: drop that newline.
        start -= 2 if text[start - 2 : start] == "\r\n" else 1
    return text[:start] + text[end:]


def _classic_block(line: str, nl: str) -> str:
    return nl.join([INCLUDE_BEGIN, line, INCLUDE_END])


def _lua_block(nl: str) -> str:
    return nl.join([LUA_BEGIN, LUA_INCLUDE_LINE, LUA_END])


def _append_block(text: str, block: Callable[[str], str]) -> str:
    """``text`` with the block at its end, in the file's own line endings.

    The block ends with a newline iff the file did, so uninstall is exact.
    """
    nl = "\r\n" if "\r\n" in text else "\n"
    had_final_nl = text == "" or text.endswith("\n")
    sep = "" if had_final_nl else nl
    return text + sep + block(nl) + (nl if had_final_nl else "")


def _copy_config(src: Path, dst: Path) -> None:
    """Copy the config folder for the checker: symlinks followed, no big files."""

    def skip(folder: str, names: list[str]) -> list[str]:
        out: list[str] = []
        for name in names:
            path = Path(folder) / name
            try:
                if path.is_dir():
                    continue
                if not path.is_file() or path.stat().st_size > _COPY_MAX_BYTES:
                    out.append(name)  # sockets, fifos, dangling links, big files
            except OSError:
                out.append(name)
        return out

    shutil.copytree(src, dst, symlinks=False, ignore=skip)


def _verify_message(output: str) -> str:
    """The checker's result lines (after "Config parsing result:"), one line."""
    _, mark, result = output.partition(_RESULT_MARK)
    text = result if mark else output
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "; ".join(lines[:5]) or "config check failed (no details)"


def _tail(res: object) -> str:
    out = getattr(res, "stderr", "") or getattr(res, "stdout", "")
    return str(out).strip()[:300]


def _tree(
    values: Mapping[str, Scalar], lua_keys: Mapping[str, KeyInfo] | None = None
) -> dict[str, object]:
    """Nested sections -> rendered value: the original keys in registry order,
    then (Lua) every other key in name order.

    Classic writes ``col.active_border`` inside ``general``; Lua nests every
    dotted part (``general = { col = { active_border = ... } }``).
    """
    tree: dict[str, object] = {}
    order = list(_KEYS)
    if lua_keys is not None:
        order += sorted(k for k in values if k in lua_keys and k not in _KEYS)
    for key in order:
        if key not in values:
            continue
        if lua_keys is not None:
            *path, leaf = key.split(".")
            rendered = lua_keys[key].lua(values[key])
        else:
            entry = _KEYS[key]
            path, leaf = list(entry.path), entry.name
            rendered = entry.spec.render(values[key])
        node = tree
        for part in path:
            child = node.setdefault(part, {})
            assert isinstance(child, dict)
            node = child
        node[leaf] = rendered
    return tree


def _render_text(values: Mapping[str, Scalar]) -> str:
    """Nested ``section { ... }`` text for all values, in registry order."""
    lines = [MANAGED_HEADER]

    def emit(node: dict[str, object], depth: int) -> None:
        pad = "    " * depth
        # Plain settings first, then sub-sections, for stable output.
        for k, v in node.items():
            if isinstance(v, str):
                lines.append(f"{pad}{k} = {v}")
        for k, v in node.items():
            if isinstance(v, dict):
                lines.append(f"{pad}{k} {{")
                emit(v, depth + 1)
                lines.append(f"{pad}}}")

    emit(_tree(values), 0)
    return "\n".join(lines) + "\n"


def _render_lua(values: Mapping[str, Scalar], keys: Mapping[str, KeyInfo]) -> str:
    """``buckwheat.lua``: the header and one ``hl.config({...})`` call."""
    lines = [LUA_MANAGED_HEADER, "hl.config({"]

    def emit(node: dict[str, object], depth: int) -> None:
        pad = "    " * depth
        # Plain settings first, then nested tables, for stable output.
        for k, v in node.items():
            if isinstance(v, str):
                lines.append(f"{pad}{k} = {v},")
        for k, v in node.items():
            if isinstance(v, dict):
                lines.append(f"{pad}{k} = {{")
                emit(v, depth + 1)
                lines.append(f"{pad}}},")

    emit(_tree(values, keys), 1)
    lines.append("})")
    return "\n".join(lines) + "\n"
