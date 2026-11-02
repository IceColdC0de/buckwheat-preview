"""Hyprland 0.57+ config keys: where they come from, which ones buckwheat sets.

Hyprland ships a typed list of every config key in its Lua stubs
(``/usr/share/hypr/stubs/hl.meta.lua``, class ``HL.ConfigValueTypes``). We
read that file when it is installed, so a newer Hyprland brings its new keys
along; otherwise we use ``hyprland_keys.json`` next to this module, generated
from the same file by ``scripts/gen_hyprland_keys.py`` (CI has no Hyprland).

Each key gets a *kind* (how buckwheat validates and writes its value). Values
travel through plans and ``managed.json`` as plain scalars, so the compound
Hyprland types are written as short strings:

- colour:   ``#rrggbb`` or ``#rrggbbaa``
- gradient: one to ten colours and an optional angle, ``#88c0d0 #b48ead 45deg``
- gap:      an integer, or a CSS shorthand string of 1-4 integers ``5 10``
- vec2:     two numbers, ``2 3``

Keys that are not about how the desktop looks, or that are risky, are left
out (``DENY_PREFIXES``, ``DENY_KEYS``, ``DENY_WORDS``).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from buckwheat.plan.models import Scalar

STUB_PATH = Path("/usr/share/hypr/stubs/hl.meta.lua")
DATA_PATH = Path(__file__).with_name("hyprland_keys.json")

Kind = Literal[
    "bool", "int", "float", "string", "color", "gradient", "gap", "vec2", "weight"
]

# Raw stub type -> kind. A type not listed here (a future Hyprland) is skipped.
_TYPE_KINDS: dict[str, Kind] = {
    "boolean": "bool",
    "integer|boolean": "int",
    "number|boolean": "float",
    "string": "string",
    "string|HL.Gradient": "gradient",
    "integer|HL.CssGap": "gap",
    "HL.Vec2Like": "vec2",
    "integer|string": "weight",
}

# Whole sections that are not about appearance, or are risky.
DENY_PREFIXES: dict[str, str] = {
    "debug.": "debugging switches",
    "ecosystem.": "permissions and Hyprland's own update/donation prompts",
    "xwayland.": "X11 compatibility, not appearance",
    "input.": "keyboard, mouse and touchpad: a bad value can lock you out",
    "input_capture.": "input handling, not appearance",
    "binds.": "key bindings behaviour, not appearance",
    "gestures.": "touchpad gestures, not appearance",
    "render.": "GPU and display pipeline: a bad value can leave a black screen",
    "opengl.": "GPU driver workarounds",
    "quirks.": "display hardware workarounds",
    "experimental.": "unfinished features",
}

# Single keys left out, with the reason.
DENY_KEYS: dict[str, str] = {
    "misc.disable_autoreload": "config autoreload",
    "misc.allow_session_lock_restore": "lock screen security",
    "misc.session_lock_xray": "lock screen security (shows windows)",
    "misc.lockdead_screen_delay": "lock screen behaviour",
    "misc.enable_anr_dialog": "starts a helper program",
    "misc.disable_hyprland_guiutils_check": "helper program check",
    "misc.disable_watchdog_warning": "start-up safety warning",
    "misc.disable_xdg_env_checks": "environment checks",
    "misc.bell_sound": "a path to a sound file that Hyprland plays",
    "misc.swallow_regex": "window swallowing rules, not appearance",
    "misc.swallow_exception_regex": "window swallowing rules, not appearance",
    "misc.key_press_enables_dpms": "screen power, not appearance",
    "misc.mouse_move_enables_dpms": "screen power, not appearance",
    "misc.vrr": "display refresh hardware",
    "decoration.screen_shader": "a path to a shader file outside buckwheat",
    "cursor.no_hardware_cursors": "display hardware",
    "cursor.use_cpu_buffer": "display hardware",
    "cursor.min_refresh_rate": "display hardware",
    "cursor.no_break_fs_vrr": "display hardware",
}

# Any key whose name contains one of these words is left out, whatever its
# section: programs, plugins, permissions and config reloading are off limits.
DENY_WORDS = ("exec", "plugin", "permission", "autoreload")

# Value ranges: the eleven original keys keep exactly their old ranges; a few
# more keys get tighter ranges than the general default because big values
# are slow to draw.
RANGES: dict[str, tuple[float, float]] = {
    "general.gaps_in": (0, 100),
    "general.gaps_out": (0, 200),
    "general.border_size": (0, 20),
    "decoration.rounding": (0, 50),
    "decoration.active_opacity": (0.0, 1.0),
    "decoration.inactive_opacity": (0.0, 1.0),
    "decoration.fullscreen_opacity": (0.0, 1.0),
    "decoration.dim_strength": (0.0, 1.0),
    "decoration.dim_special": (0.0, 1.0),
    "decoration.dim_around": (0.0, 1.0),
    "decoration.rounding_power": (1.0, 10.0),
    "decoration.blur.size": (1, 20),
    "decoration.blur.passes": (1, 8),
    "decoration.shadow.range": (0, 100),
    "decoration.shadow.render_power": (1, 4),
    "decoration.glow.range": (0, 100),
    "decoration.glow.render_power": (1, 4),
    "decoration.motion_blur.samples": (1, 32),
    "decoration.wobble.mesh": (2, 64),
    "group.groupbar.font_size": (4, 72),
    "group.groupbar.height": (0, 100),
}
DEFAULT_INT_RANGE = (-10000, 10000)
DEFAULT_FLOAT_RANGE = (-10000.0, 10000.0)
MAX_STRING = 100
MAX_GRADIENT_COLORS = 10

_STUB_FIELD = re.compile(r"^---@field \['([a-z0-9_.]+)'\] (\S+)\s*$")
_HEX = r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?"
_COLOR = re.compile(rf"^{_HEX}$")
_ANGLE = re.compile(r"^(-?\d{1,3})deg$")
_NUMBER = re.compile(r"^-?\d{1,5}(?:\.\d{1,4})?$")
_INT = re.compile(r"^\d{1,4}$")
# Plain strings (layout names, font families, "0.5, 1.0" lists): no quotes,
# backslashes, brackets or anything else that could break out of a Lua string.
_PLAIN = re.compile(r"^[A-Za-z0-9 ._+,:%-]*$")
_WEIGHTS = (
    "thin",
    "ultralight",
    "light",
    "semilight",
    "book",
    "normal",
    "medium",
    "semibold",
    "bold",
    "ultrabold",
    "heavy",
    "ultraheavy",
)

KIND_HELP: dict[Kind, str] = {
    "bool": "true/false",
    "int": "integer",
    "float": "number",
    "string": "text",
    "color": '"#rrggbb" or "#rrggbbaa"',
    "gradient": 'colour, or gradient "#rrggbb #rrggbb 45deg"',
    "gap": 'integer, or CSS-style "top right bottom left"',
    "vec2": 'two numbers "x y"',
    "weight": "100-1000 or thin/light/normal/medium/semibold/bold/heavy",
}


def parse_stub(text: str) -> dict[str, str]:
    """Key -> raw stub type, from the ``HL.ConfigValueTypes`` class."""
    keys: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        if line.startswith("---@class "):
            inside = line.split()[1] == "HL.ConfigValueTypes"
            continue
        if not inside:
            continue
        if not line.startswith("---@"):
            break  # the class's field block ends at the first non-annotation
        match = _STUB_FIELD.match(line)
        if match:
            keys[match.group(1)] = match.group(2)
    return keys


def _read_stub(path: Path) -> dict[str, str] | None:
    try:
        keys = parse_stub(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return keys or None


def _read_data(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    keys = raw["keys"]
    return {str(k): str(v) for k, v in keys.items()}


@lru_cache(maxsize=4)
def raw_key_types(
    stub: Path | None = STUB_PATH, data: Path = DATA_PATH
) -> dict[str, str]:
    """Every key Hyprland knows -> raw type; installed stub first, then our copy."""
    if stub is not None:
        found = _read_stub(stub)
        if found is not None:
            return found
    return _read_data(data)


def deny_reason(key: str) -> str | None:
    """Why buckwheat never sets ``key``, or None if it may."""
    for prefix, why in DENY_PREFIXES.items():
        if key.startswith(prefix):
            return why
    if key in DENY_KEYS:
        return DENY_KEYS[key]
    for word in DENY_WORDS:
        if word in key:
            return f"names {word!r}: off limits"
    return None


def _is_color_name(key: str) -> bool:
    leaf = key.rsplit(".", 1)[-1]
    return ".col." in f".{key}" or "color" in leaf or leaf == "tint"


def kind_of(key: str, raw: str) -> Kind | None:
    kind = _TYPE_KINDS.get(raw)
    if kind == "string" and _is_color_name(key):
        return "color"
    return kind


@dataclass(frozen=True)
class KeyInfo:
    key: str
    kind: Kind

    def check(self, value: Scalar) -> str | None:
        """A problem with ``value`` (one short sentence), or None if it is fine."""
        return _check(self.key, self.kind, value)

    def lua(self, value: Scalar) -> str:
        """``value`` as a Lua expression (only called on checked values)."""
        return _lua(self.kind, value)


def allowed_keys(raw: Mapping[str, str]) -> dict[str, KeyInfo]:
    """The keys buckwheat may set: known type and not denied."""
    out: dict[str, KeyInfo] = {}
    for key, typ in sorted(raw.items()):
        kind = kind_of(key, typ)
        if kind is None or deny_reason(key) is not None:
            continue
        out[key] = KeyInfo(key, kind)
    return out


# --- validation -------------------------------------------------------------


def _in_range(key: str, value: float, default: tuple[float, float]) -> str | None:
    lo, hi = RANGES.get(key, default)
    if not lo <= value <= hi:
        return f"{key}: must be between {lo:g} and {hi:g}"
    return None


def _is_int(value: Scalar) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check(key: str, kind: Kind, value: Scalar) -> str | None:
    if kind == "bool":
        return None if isinstance(value, bool) else f"{key}: expected true or false"
    if kind == "int":
        if not _is_int(value):
            return f"{key}: expected an integer"
        return _in_range(key, float(value), DEFAULT_INT_RANGE)
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return f"{key}: expected a number"
        if not math.isfinite(value):
            return f"{key}: must be finite"
        return _in_range(key, float(value), DEFAULT_FLOAT_RANGE)
    if kind == "gap":
        if _is_int(value):
            return _in_range(key, float(value), (0, 500))
        parts = str(value).split() if isinstance(value, str) else []
        if not 1 <= len(parts) <= 4 or not all(_INT.match(p) for p in parts):
            return f'{key}: expected an integer or "top right bottom left"'
        for p in parts:
            if (problem := _in_range(key, float(p), (0, 500))) is not None:
                return problem
        return None
    if kind == "weight" and _is_int(value):
        return _in_range(key, float(value), (100, 1000))
    if not isinstance(value, str):
        return f"{key}: expected {KIND_HELP[kind]}"
    if kind == "color":
        return None if _COLOR.match(value) else f"{key}: use #rrggbb or #rrggbbaa"
    if kind == "gradient":
        if _gradient(value) is None:
            return f'{key}: use #rrggbb, or a gradient like "#rrggbb #rrggbb 45deg"'
        return None
    if kind == "vec2":
        parts = value.split()
        if len(parts) != 2 or not all(_NUMBER.match(p) for p in parts):
            return f'{key}: expected two numbers "x y"'
        return None
    if kind == "weight":
        return None if value in _WEIGHTS else f"{key}: expected 100-1000 or a name"
    # plain string
    if len(value) > MAX_STRING or not _PLAIN.match(value):
        return f"{key}: only letters, digits, spaces and . _ + , : % - are allowed"
    return None


def _gradient(value: str) -> tuple[list[str], int | None] | None:
    """(colours, angle) for a gradient string, or None if it is not one."""
    parts = value.split()
    angle: int | None = None
    if parts and (m := _ANGLE.match(parts[-1])):
        angle = int(m.group(1))
        if not -360 <= angle <= 360:
            return None
        parts = parts[:-1]
    if not 1 <= len(parts) <= MAX_GRADIENT_COLORS:
        return None
    if not all(_COLOR.match(p) for p in parts):
        return None
    if angle is not None and len(parts) < 2:
        return None  # an angle needs at least two colours
    return parts, angle


# --- Lua rendering ----------------------------------------------------------


def lua_color(value: str) -> str:
    """``#rrggbb`` -> ``"rgb(rrggbb)"``; ``#rrggbbaa`` -> ``"rgba(rrggbbaa)"``."""
    hexpart = value[1:].lower()
    func = "rgb" if len(hexpart) == 6 else "rgba"
    return f'"{func}({hexpart})"'


def lua_string(value: str) -> str:
    """A Lua string literal. Values are checked first; escape anyway."""
    out = value.replace("\\", "\\\\").replace('"', '\\"')
    out = "".join(c if c.isprintable() else " " for c in out)
    return f'"{out}"'


def _num(value: Scalar) -> str:
    return repr(float(value)) if isinstance(value, float) else str(value)


def _lua(kind: Kind, value: Scalar) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if kind in ("int", "float"):
        return _num(value)
    if kind == "gap":
        if not isinstance(value, str):
            return _num(value)
        nums = [int(p) for p in value.split()]
        # CSS shorthand: 1 = all, 2 = vertical horizontal, 3 = top h bottom.
        top, right, bottom, left = {
            1: lambda n: (n[0], n[0], n[0], n[0]),
            2: lambda n: (n[0], n[1], n[0], n[1]),
            3: lambda n: (n[0], n[1], n[2], n[1]),
            4: lambda n: (n[0], n[1], n[2], n[3]),
        }[len(nums)](nums)
        return f"{{ top = {top}, right = {right}, bottom = {bottom}, left = {left} }}"
    if kind == "weight":
        return _num(value) if not isinstance(value, str) else lua_string(value)
    text = str(value)
    if kind == "color":
        return lua_color(text)
    if kind == "gradient":
        parsed = _gradient(text)
        assert parsed is not None
        colors, angle = parsed
        if angle is None and len(colors) == 1:
            return lua_color(colors[0])
        listed = ", ".join(lua_color(c) for c in colors)
        tail = f", angle = {angle}" if angle is not None else ""
        return f"{{ colors = {{ {listed} }}{tail} }}"
    if kind == "vec2":
        a, b = text.split()
        return f"{{ {a}, {b} }}"
    return lua_string(text)


# --- planner prompt ---------------------------------------------------------

_KIND_TAGS: dict[Kind, str] = {
    "bool": "bool",
    "int": "int",
    "float": "num",
    "string": "text",
    "color": "colour",
    "gradient": "gradient",
    "gap": "gap",
    "vec2": "vec2",
    "weight": "weight",
}


def describe(keys: Mapping[str, KeyInfo]) -> str:
    """Compact key list for the planner: one line per section, grouped by type.

    ``decoration: bool dim_inactive, shadow.{enabled,sharp}; int rounding``
    """
    sections: dict[str, dict[Kind, dict[str, list[str]]]] = {}
    for key, info in sorted(keys.items()):
        section, _, rest = key.partition(".")
        sub, _, leaf = rest.rpartition(".")
        subs_of = sections.setdefault(section, {}).setdefault(info.kind, {})
        subs_of.setdefault(sub, []).append(leaf)
    help_text = "; ".join(f"{_KIND_TAGS[k]} = {h}" for k, h in KIND_HELP.items())
    lines = [f"Value types: {help_text}"]
    for section, by_kind in sections.items():
        groups: list[str] = []
        for kind in _KIND_TAGS:
            subs = by_kind.get(kind)
            if not subs:
                continue
            names: list[str] = []
            for sub, leaves in subs.items():
                if not sub:
                    names.extend(leaves)
                elif len(leaves) == 1:
                    names.append(f"{sub}.{leaves[0]}")
                else:
                    names.append(f"{sub}.{{{','.join(leaves)}}}")
            groups.append(f"{_KIND_TAGS[kind]} {', '.join(names)}")
        lines.append(f"{section}: " + "; ".join(groups))
    return "\n".join(lines)
