"""waybar adapter (design.md section 3.4).

Managed files: ``buckwheat.jsonc`` (pulled in by an ``"include"`` entry in the
user's config) and ``buckwheat.css`` (pulled in by an ``@import`` at the end of
``style.css``). ``render``, ``install`` and ``uninstall`` only return text; the
session writes it through ``safety.atomic_write``.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
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
    merge_state,
    register,
)
from buckwheat.config import _xdg_dir
from buckwheat.plan.context import Problem
from buckwheat.plan.models import FontOp, Op, PaletteOp, Scalar, SetOp
from buckwheat.plan.palettes import PaletteError, load_palette
from buckwheat.plan.validators import COLOR_RE, FONT_FAMILY_RE

RELOAD_WAIT = 1.0
# A double-quoted string on one line: a JSON value or a CSS ``url("...")`` argument.
_QUOTED = re.compile(r'"([^"]*)"')

# Palette slot -> waybar colour key (rendered as ``@define-color <name>``).
COLOR_SLOTS = (
    "bg",
    "bg_alt",
    "surface",
    "fg",
    "fg_dim",
    "accent",
    "accent_alt",
    "warning",
    "error",
)

REGISTRY: dict[str, KeySpec] = {
    "bar.position": KeySpec(str, enum=("top", "bottom", "left", "right")),
    "bar.height": KeySpec(int, min=10, max=200),
    "bar.spacing": KeySpec(int, min=0, max=50),
    "bar.layer": KeySpec(str, enum=("top", "bottom", "overlay")),
    "font_family": KeySpec(str),
    "font_size": KeySpec(float, min=6, max=48, render=lambda v: f"{v:g}px"),
    **{f"color.{slot}": KeySpec(str) for slot in COLOR_SLOTS},
}


def _problem(code: str, message: str) -> Problem:
    return Problem(code=code, message=message)


def _check_value(key: str, value: Scalar) -> list[Problem]:
    """Registry check plus the value rules for colours and font families."""
    spec = REGISTRY.get(key)
    if spec is None:
        return [_problem("unknown_key", f"waybar: key {key!r} is not settable")]
    problems = spec.check(key, value)
    if problems:
        return problems
    if key.startswith("color.") and not COLOR_RE.match(str(value)):
        return [_problem("bad_color", f"{key}: expected #rrggbb")]
    if key == "font_family" and not FONT_FAMILY_RE.match(str(value)):
        return [_problem("bad_font", "font_family: unsupported characters")]
    return []


def _ops_to_values(ops: list[Op]) -> tuple[dict[str, Scalar], list[Problem]]:
    """Turn ops for waybar into registry key/values. Other targets are skipped."""
    values: dict[str, Scalar] = {}
    problems: list[Problem] = []
    for op in ops:
        if isinstance(op, SetOp) and op.target == "waybar":
            problems += _check_value(op.key, op.value)
            values[op.key] = op.value
        elif isinstance(op, FontOp) and op.target == "waybar":
            if op.role == "terminal":
                problems.append(_problem("bad_role", "waybar has no terminal font"))
                continue
            problems += _check_value("font_family", op.family)
            values["font_family"] = op.family
            if op.size is not None:
                problems += _check_value("font_size", op.size)
                values["font_size"] = op.size
        elif isinstance(op, PaletteOp) and "waybar" in op.targets:
            try:
                pal = load_palette(op.palette, op.variant)
            except PaletteError as exc:
                problems.append(_problem("unknown_palette", str(exc)))
                continue
            for slot in COLOR_SLOTS:
                values[f"color.{slot}"] = getattr(pal.colors, slot)
    return values, problems


# --- text helpers -----------------------------------------------------------


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _jsonc_header() -> str:
    return "// " + MANAGED_HEADER.removeprefix("# ")


def _css_block(nl: str, import_line: str) -> str:
    """The block appended to style.css. Removal is an exact string match."""
    return f"{nl}/* {INCLUDE_BEGIN} */{nl}{import_line}{nl}/* {INCLUDE_END} */{nl}"


def _json_block(nl: str, path: Path, trailing_comma: bool) -> str:
    comma = "," if trailing_comma else ""
    return (
        f"{nl}  // {INCLUDE_BEGIN}{nl}"
        f'  "include": [{json.dumps(str(path))}]{comma}{nl}'
        f"  // {INCLUDE_END}"
    )


def _skip_comments(text: str, pos: int) -> int:
    """Index of the first character at/after ``pos`` that is not space or comment."""
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
        elif text.startswith("//", pos):
            end = text.find("\n", pos)
            pos = len(text) if end == -1 else end + 1
        elif text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            if end == -1:
                return len(text)
            pos = end + 2
        else:
            break
    return pos


def _object_start(text: str) -> int | None:
    """Index just after the top-level ``{`` of a JSONC object, else None."""
    pos = _skip_comments(text, 0)
    if pos < len(text) and text[pos] == "{":
        return pos + 1
    return None


@register("waybar")
class WaybarAdapter:
    name: ClassVar[str] = "waybar"
    # Default locations; an instance built with another config dir overrides
    # them in ``self.files`` (the instance's own allowlist).
    managed_files: ClassVar[list[Path]] = [
        _xdg_dir("XDG_CONFIG_HOME", ".config") / "waybar" / "buckwheat.jsonc",
        _xdg_dir("XDG_CONFIG_HOME", ".config") / "waybar" / "buckwheat.css",
    ]

    def __init__(
        self,
        config_dir: Path | None = None,
        runner: CommandRunner | None = None,
        sleep: Callable[[float], None] = time.sleep,
        saved: Mapping[str, Scalar] | None = None,
    ) -> None:
        self.dir = config_dir or _xdg_dir("XDG_CONFIG_HOME", ".config") / "waybar"
        self.runner: CommandRunner = runner or SubprocessRunner()
        self._sleep = sleep
        self._saved = dict(saved or {})
        self.json_path = self.dir / "buckwheat.jsonc"
        self.css_path = self.dir / "buckwheat.css"
        self.style_path = self.dir / "style.css"
        self.files = [self.json_path, self.css_path]
        self._needs_restart = False

    # -- detection and state -------------------------------------------------

    def _user_config(self) -> Path | None:
        for name in ("config.jsonc", "config"):
            if (self.dir / name).is_file():
                return self.dir / name
        return None

    def _read(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            with open(path, encoding="utf-8", newline="") as f:  # keep CRLF
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def detect(self) -> Detection:
        version: str | None = None
        installed = False
        try:
            res = self.runner.run(["waybar", "--version"])
        except CommandError:
            res = None
        if res is not None and res.returncode == 0:
            installed = True
            m = re.search(r"v?(\d+\.\d+(?:\.\d+)?)", res.stdout)
            version = m.group(1) if m else None
        return Detection(
            installed=installed,
            version=version,
            config_dir=self.dir if self.dir.is_dir() else None,
            running=self._running(),
        )

    def _running(self) -> bool:
        argv = ["pgrep", "-U", str(os.getuid()), "-x", "waybar"]
        try:
            return self.runner.run(argv).returncode == 0
        except CommandError:
            return False

    def read_state(self) -> TargetState:
        cfg = self._read(self._user_config())
        css = self._read(self.style_path)
        has_json = cfg is not None and INCLUDE_BEGIN in cfg
        has_css = css is not None and INCLUDE_BEGIN in css
        status: BootstrapStatus
        if has_json and has_css:
            status = "bootstrapped"
        elif has_json or has_css or (cfg is not None and _object_start(cfg) is None):
            status = "weird"
        else:
            status = "none"
        return TargetState(values=dict(self._saved), bootstrap=status)

    # -- plan handling -------------------------------------------------------

    def validate(self, op: Op) -> list[Problem]:
        return _ops_to_values([op])[1]

    def allowed_keys(self) -> list[str]:
        return sorted(REGISTRY)

    def state_updates(
        self, ops: Sequence[Op], state: TargetState
    ) -> Mapping[str, Scalar]:
        """Registry key -> value that ``ops`` write (set, font and palette ops)."""
        updates, problems = _ops_to_values(list(ops))
        if problems:
            raise ValueError("; ".join(p.message for p in problems))
        return updates

    def render(self, ops: list[Op], state: TargetState) -> RenderResult:
        updates = self.state_updates(ops, state)
        values = merge_state(state.values, updates)
        return RenderResult(
            {
                self.json_path: self._render_json(values),
                self.css_path: self._render_css(values),
            }
        )

    def _render_json(self, values: Mapping[str, Scalar]) -> str:
        bar = {
            key.removeprefix("bar."): values[key]
            for key in sorted(values)
            if key.startswith("bar.") and key in REGISTRY
        }
        return _jsonc_header() + "\n" + json.dumps(bar, indent=2) + "\n"

    def _render_css(self, values: Mapping[str, Scalar]) -> str:
        out = [f"/* {MANAGED_HEADER.removeprefix('# ')} */\n"]
        for slot in COLOR_SLOTS:
            if f"color.{slot}" in values:
                out.append(f"@define-color {slot} {values[f'color.{slot}']};\n")
        star: list[str] = []
        if "font_family" in values:
            star.append(f'  font-family: "{values["font_family"]}";\n')
        if "font_size" in values:
            size = REGISTRY["font_size"].render(values["font_size"])
            star.append(f"  font-size: {size};\n")
        if star:
            out.append("* {\n" + "".join(star) + "}\n")
        win = []
        if "color.bg" in values:
            win.append("  background-color: @bg;\n")
        if "color.fg" in values:
            win.append("  color: @fg;\n")
        if win:
            out.append("window#waybar {\n" + "".join(win) + "}\n")
        return "".join(out)

    # -- reload --------------------------------------------------------------

    def reload(self) -> ReloadResult:
        if not self._running():
            if self._needs_restart:
                self._needs_restart = False
                try:
                    self.runner.spawn(["waybar"])
                except CommandError as exc:
                    return ReloadResult(False, f"could not restart waybar: {exc}")
                return ReloadResult(True, "restarted waybar")
            return ReloadResult(True, "waybar is not running; nothing to reload")
        argv = ["pkill", "-USR2", "-U", str(os.getuid()), "-x", "waybar"]
        try:
            res = self.runner.run(argv)
        except CommandError as exc:
            return ReloadResult(False, str(exc))
        if res.returncode != 0:
            return ReloadResult(False, "could not signal waybar")
        self._sleep(RELOAD_WAIT)
        if not self._running():
            self._needs_restart = True
            return ReloadResult(False, "waybar exited after reload (bad config/CSS?)")
        self._needs_restart = False
        return ReloadResult(True)

    # -- bootstrap -----------------------------------------------------------

    def bootstrap_plan(self) -> BootstrapPlan | None:
        """The include the user confirms; None if there is no config to edit."""
        cfg = self._user_config()
        if cfg is None:
            return None
        return BootstrapPlan(cfg, f'"include": [{json.dumps(str(self.json_path))}]')

    def bootstrap_files(self) -> list[Path]:
        """Both user files install may edit: the JSON config and style.css."""
        return [self.dir / "config.jsonc", self.dir / "config", self.style_path]

    def install(self, plan: BootstrapPlan) -> RenderResult:
        """Add the JSON include and the CSS import. Refuses unusual configs."""
        cfg = self._read(plan.file)
        if cfg is None:
            raise ValueError(f"cannot read {plan.file}")
        if INCLUDE_BEGIN in cfg:
            raise ValueError("waybar config already has a buckwheat include")
        start = _object_start(cfg)
        if start is None:
            raise ValueError("waybar config is not a single JSON object")
        if re.search(r'"include"\s*:', cfg):
            raise ValueError('waybar config already has its own "include"')
        nl = _newline(cfg)
        rest = cfg[_skip_comments(cfg, start) :]
        block = _json_block(nl, self.json_path, not rest.startswith("}"))
        files: dict[Path, str] = {plan.file: cfg[:start] + block + cfg[start:]}

        css = self._read(self.style_path) or ""
        if INCLUDE_BEGIN not in css:
            import_line = '@import url("buckwheat.css");'
            css_nl = _newline(css) if css else nl
            files[self.style_path] = css + _css_block(css_nl, import_line)
        return RenderResult(files)

    def uninstall(self) -> RenderResult:
        """Strip the include blocks and delete our managed files.

        User files are left as they were, apart from the blocks. A managed file is
        only deleted if it carries buckwheat's header. Any line still left in a
        user file that names a managed file is reported as a leftover.
        """
        files: dict[Path, str] = {}
        leftovers: list[str] = []
        cfg_path = self._user_config()
        cfg = self._read(cfg_path)
        if cfg_path is not None and cfg is not None:
            cfg_after = cfg
            if INCLUDE_BEGIN in cfg:
                nl = _newline(cfg)
                for comma in (True, False):
                    block = _json_block(nl, self.json_path, comma)
                    if block in cfg:
                        cfg_after = cfg.replace(block, "", 1)
                        files[cfg_path] = cfg_after
                        break
            leftovers += self._stray_includes(cfg_path, cfg_after, self.json_path)
        css = self._read(self.style_path)
        if css is not None:
            css_after = css
            if INCLUDE_BEGIN in css:
                block = _css_block(_newline(css), '@import url("buckwheat.css");')
                if block in css:
                    css_after = css.replace(block, "", 1)
                    files[self.style_path] = css_after
            leftovers += self._stray_includes(self.style_path, css_after, self.css_path)
        deletes = frozenset(p for p in self.files if is_managed_file(p))
        return RenderResult(files, deletes, leftovers=tuple(leftovers))

    def _stray_includes(self, user_file: Path, text: str, managed: Path) -> list[str]:
        """One leftover message per line of ``text`` that names ``managed``."""
        out: list[str] = []
        for line in text.splitlines():
            for value in _QUOTED.findall(line):
                if self.dir / Path(value).expanduser() == managed:
                    out.append(
                        f"{user_file.name} still has the line `{line.strip()}`, "
                        f"which points at {managed.name}. Buckwheat only edits its "
                        "own marked block, so remove that line by hand."
                    )
                    break
        return out
