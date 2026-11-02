"""Alacritty adapter.

Managed settings live in ``buckwheat.toml`` beside the ``alacritty.toml`` that
alacritty reads (see ``_conf_dir``; normally ``~/.config/alacritty``). It is a
file this adapter owns and fully regenerates from the managed state file. It
takes effect through one ``import`` entry in the user's ``alacritty.toml``.

Alacritty gives the main file priority over imported files, so any key the user
already set there silently beats ours. ``read_state`` finds those keys and
reports them as ``shadowed_keys``; the user's file is never edited to fix it.
The only edit to the user's file is the include entry (``install``), and
``uninstall`` takes exactly that entry back out.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

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
    load_managed_state,
    merge_state,
    register,
)
from buckwheat.config import _xdg_dir
from buckwheat.plan.context import Problem
from buckwheat.plan.models import (
    ChangePlan,
    FontOp,
    Op,
    PaletteOp,
    Scalar,
    SetOp,
)
from buckwheat.plan.palettes import PaletteError, PaletteVariant, load_palette
from buckwheat.plan.validators import COLOR_RE, FONT_FAMILY_RE, validate_common


def _conf_dirs() -> list[Path]:
    """The directories alacritty looks in for ``alacritty.toml``, in its order.

    ``$XDG_CONFIG_HOME/alacritty``, then ``$XDG_CONFIG_HOME``, then
    ``~/.config/alacritty``. An unset, empty or relative ``$XDG_CONFIG_HOME``
    counts as ``~/.config``, as it does in alacritty (see ``_xdg_dir``).
    Not in this list: ``$XDG_CONFIG_DIRS``, ``~/.alacritty.toml`` and ``ETC_CONF``,
    which alacritty also checks. ``install`` refuses when one of those is the file
    alacritty reads (see ``_effective_conf``).
    """
    base = _xdg_dir("XDG_CONFIG_HOME", ".config")
    return [base / "alacritty", base, Path.home() / ".config" / "alacritty"]


def _has_conf(conf_dir: Path) -> bool:
    """Whether ``conf_dir`` holds an ``alacritty.toml``. Errors count as absent."""
    try:
        return (conf_dir / "alacritty.toml").exists()
    except OSError:
        return False


def _conf_dir() -> Path:
    """The directory alacritty reads its ``alacritty.toml`` from.

    Looked up on each call, not once at import, so a changed HOME or
    ``$XDG_CONFIG_HOME`` is honoured. It is the first of ``_conf_dirs()`` that
    holds an ``alacritty.toml``. When none does, it is the first one: alacritty
    checks that directory first, so the file buckwheat creates there is read by
    alacritty unless another file comes earlier in its order (``install`` checks).
    """
    dirs = _conf_dirs()
    for conf_dir in dirs:
        if _has_conf(conf_dir):
            return conf_dir
    return dirs[0]


# alacritty's last place for a config file. Only in alacritty master, not 0.14.0.
ETC_CONF = Path("/etc/alacritty/alacritty.toml")


def _xdg_config_dirs() -> list[Path]:
    """``$XDG_CONFIG_DIRS`` as the xdg crate reads it: absolute entries only.

    Relative entries are dropped. Unset or empty means ``/etc/xdg``.
    """
    raw = os.environ.get("XDG_CONFIG_DIRS", "")
    if not raw:
        return [Path("/etc/xdg")]
    return [Path(entry) for entry in raw.split(":") if os.path.isabs(entry)]


def _conf_candidates(suffix: str) -> list[Path]:
    """Every path alacritty checks for ``alacritty.<suffix>``, in its order.

    ``suffix`` is ``toml`` or ``yml``. Follows ``installed_config()`` in alacritty
    and the xdg crate it uses.
    """
    name = f"alacritty.{suffix}"
    xdg_home = _xdg_dir("XDG_CONFIG_HOME", ".config")
    sys_dirs = _xdg_config_dirs()
    return [
        xdg_home / "alacritty" / name,
        *(d / "alacritty" / name for d in sys_dirs),
        xdg_home / name,
        *(d / name for d in sys_dirs),
        Path.home() / ".config" / "alacritty" / name,
        Path.home() / f".{name}",
        # Beside ETC_CONF, so patching ETC_CONF (as the tests do) moves this too.
        ETC_CONF.parent / name,
    ]


def _effective_conf() -> Path | None:
    """The file alacritty reads, or None.

    As alacritty's ``load()`` does: the first ``alacritty.toml`` in the order of
    ``_conf_candidates``. Only when there is none does the first ``alacritty.yml``
    in that order count. A path that cannot be checked counts as absent, as in
    ``_has_conf``.
    """
    for suffix in ("toml", "yml"):
        for path in _conf_candidates(suffix):
            try:
                if path.exists():
                    return path
            except OSError:
                continue
    return None


# Before this version the import list was a top-level key, not ``[general]``.
GENERAL_IMPORT_SINCE = (0, 14)


# --- TOML emitter (scalars and plain tables only) -------------------------


def toml_str(value: Scalar) -> str:
    """Render ``value`` as a TOML basic string. The only place strings are escaped."""
    out = ['"']
    for ch in str(value):
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ord(ch) < 32 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _toml_float(value: Scalar) -> str:
    return repr(float(value))


def _toml_int(value: Scalar) -> str:
    return str(int(value))


# --- key registry and palette slot map ------------------------------------

_ANSI_NAMES = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")


def _build_registry() -> dict[str, KeySpec]:
    reg: dict[str, KeySpec] = {
        "font.size": KeySpec(float, min=6, max=48, render=_toml_float),
        "font.normal.family": KeySpec(str, render=toml_str),
        "window.opacity": KeySpec(float, min=0, max=1, render=_toml_float),
        "window.padding.x": KeySpec(int, min=0, max=100, render=_toml_int),
        "window.padding.y": KeySpec(int, min=0, max=100, render=_toml_int),
        "colors.primary.background": KeySpec(str, render=toml_str),
        "colors.primary.foreground": KeySpec(str, render=toml_str),
        "colors.cursor.text": KeySpec(str, render=toml_str),
        "colors.cursor.cursor": KeySpec(str, render=toml_str),
    }
    for group in ("normal", "bright"):
        for name in _ANSI_NAMES:
            reg[f"colors.{group}.{name}"] = KeySpec(str, render=toml_str)
    return reg


REGISTRY: dict[str, KeySpec] = _build_registry()

# key -> palette slot ("colors.<slot>" or "ansi.<slot>" on a PaletteVariant).
PALETTE_SLOTS: dict[str, str] = {
    "colors.primary.background": "colors.bg",
    "colors.primary.foreground": "colors.fg",
    "colors.cursor.text": "colors.bg",
    "colors.cursor.cursor": "colors.cursor",
    **{f"colors.normal.{n}": f"ansi.{n}" for n in _ANSI_NAMES},
    **{f"colors.bright.{n}": f"ansi.bright_{n}" for n in _ANSI_NAMES},
}


def _slot_value(palette: PaletteVariant, slot: str) -> str:
    group, name = slot.split(".")
    value = getattr(getattr(palette, group), name)
    assert isinstance(value, str)
    return value


def _value_ok(key: str, value: Scalar) -> bool:
    spec = REGISTRY.get(key)
    if spec is None or spec.check(key, value):
        return False
    if key.startswith("colors."):
        return isinstance(value, str) and COLOR_RE.fullmatch(value) is not None
    if key == "font.normal.family":
        return isinstance(value, str) and FONT_FAMILY_RE.fullmatch(value) is not None
    return True


def _clean(values: Mapping[str, Scalar]) -> dict[str, Scalar]:
    """Keep only registry keys with valid values (state files can be stale)."""
    return {k: v for k, v in values.items() if _value_ok(k, v)}


def format_managed(values: Mapping[str, Scalar]) -> str:
    """The full text of buckwheat.toml for ``values``, in registry order."""
    tables: dict[str, list[str]] = {}
    for key, spec in REGISTRY.items():
        if key in values:
            table, _, leaf = key.rpartition(".")
            tables.setdefault(table, []).append(f"{leaf} = {spec.render(values[key])}")
    lines = [MANAGED_HEADER]
    for table, items in tables.items():
        lines += ["", f"[{table}]", *items]
    return "\n".join(lines) + "\n"


# --- text helpers for the one include edit --------------------------------

_LINE_RE = re.compile(r"[^\n]*\n|[^\n]+")
_GENERAL_HEADER_RE = re.compile(r"^[ \t]*\[general\][ \t]*(#.*)?$")
# A single-line array with no comment: ``import = [ ... ]``.
_IMPORT_LINE_RE = re.compile(r"^([ \t]*import[ \t]*=[ \t]*\[)([^\]#]*)(\][ \t]*)$")


def _split_lines(text: str) -> list[str]:
    """Split keeping each line's terminator (only ``\\n`` and ``\\r\\n``)."""
    return _LINE_RE.findall(text)


def _body(line: str) -> str:
    return line.rstrip("\r\n")


def _insert_block(lines: list[str], at: int, block: list[str], nl: str) -> list[str]:
    """Insert ``block`` before ``lines[at]``.

    At the end of a file with no final newline the separator is added first and
    the block's last line is left unterminated, so removal can restore the
    original bytes.
    """
    out = list(lines)
    if at > 0 and not out[at - 1].endswith("\n"):
        out[at - 1] += nl
        new = [b + nl for b in block[:-1]] + [block[-1]]
    else:
        new = [b + nl for b in block]
    out[at:at] = new
    return out


def _remove_block(lines: list[str]) -> list[str] | None:
    """Remove the marked include block; None if there is none."""
    bodies = [_body(line).strip() for line in lines]
    if INCLUDE_BEGIN not in bodies:
        return None
    start = bodies.index(INCLUDE_BEGIN)
    if INCLUDE_END not in bodies[start:]:
        return None
    end = bodies.index(INCLUDE_END, start)
    out = lines[:start] + lines[end + 1 :]
    if not lines[end].endswith("\n") and start > 0:
        out[start - 1] = out[start - 1].removesuffix("\n").removesuffix("\r")
    return out


class AlacrittyAdapter:
    name: ClassVar[str] = "alacritty"
    # Only our own files: buckwheat.toml beside each alacritty.toml that
    # _conf_dir() can pick. The user's alacritty.toml is the bootstrap file
    # (bootstrap_files), writable only for the include. Computed at import, as
    # for the other adapters, so XDG_CONFIG_HOME must be set before startup.
    managed_files: ClassVar[list[Path]] = [
        d / "buckwheat.toml" for d in dict.fromkeys(_conf_dirs())
    ]

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        user_conf: Path | None = None,
        managed_conf: Path | None = None,
        state_path: Path | None = None,
        home: Path | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.runner = runner or SubprocessRunner()
        conf_dir = _conf_dir()  # read per instance, not at import
        self.user_conf = user_conf or conf_dir / "alacritty.toml"
        # When the caller did not pick user_conf, install() must check that it is
        # the file alacritty reads. A caller that picked it chose it on purpose.
        self._user_conf_default = user_conf is None
        self.managed_conf = managed_conf or conf_dir / "buckwheat.toml"
        self.state_path = state_path
        self.home = home if home is not None else Path.home()
        self.which = which

    # --- reading the files ---------------------------------------------------

    def _read_user(self) -> tuple[str, dict[str, Any]]:
        """Return the user file's text and parsed data; ValueError if unusable."""
        try:
            text = self.user_conf.read_bytes().decode("utf-8")  # keep CRLF
        except FileNotFoundError:
            return "", {}
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"cannot read {self.user_conf}: {exc}") from exc
        try:
            return text, tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"cannot parse {self.user_conf}: {exc}") from exc

    def _managed_ownership(self) -> BootstrapStatus:
        """Is buckwheat.toml ours? ``user-shadowed`` here means a user-owned file."""
        try:
            text = self.managed_conf.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "none"
        except (OSError, UnicodeDecodeError):
            return "weird"
        if not text.strip():
            return "none"
        return "bootstrapped" if text.startswith(MANAGED_HEADER) else "user-shadowed"

    def _entry(self) -> str:
        """The import path as written in alacritty.toml (``~`` when under home)."""
        try:
            return "~/" + self.managed_conf.relative_to(self.home).as_posix()
        except ValueError:
            return str(self.managed_conf)

    def _import_line(self) -> str:
        return f"import = [{toml_str(self._entry())}]"

    def _expand(self, entry: str) -> Path:
        if entry == "~" or entry.startswith("~/"):
            return self.home / entry[2:]
        path = Path(entry)
        return path if path.is_absolute() else self.user_conf.parent / path

    def _has_include(self, data: Mapping[str, Any]) -> bool:
        general = data.get("general")
        lists = [data.get("import")]
        if isinstance(general, dict):
            lists.append(general.get("import"))
        return any(
            isinstance(entries, list)
            and any(
                isinstance(e, str) and self._expand(e) == self.managed_conf
                for e in entries
            )
            for entries in lists
        )

    def _managed_values(self) -> dict[str, Scalar]:
        return _clean(load_managed_state(self.state_path).get(self.name, {}))

    def _version(self) -> tuple[int, int, int] | None:
        try:
            res = self.runner.run(["alacritty", "--version"])
        except CommandError:
            return None
        m = (
            re.search(r"(\d+)\.(\d+)\.(\d+)", res.stdout)
            if res.returncode == 0
            else None
        )
        return (int(m[1]), int(m[2]), int(m[3])) if m else None

    # --- Adapter protocol ----------------------------------------------------

    def detect(self) -> Detection:
        installed = self.which("alacritty") is not None
        version: str | None = None
        running = False
        if installed:
            v = self._version()
            version = ".".join(map(str, v)) if v else None
            try:
                running = self.runner.run(["pgrep", "-x", "alacritty"]).returncode == 0
            except CommandError:
                running = False
        return Detection(
            installed=installed,
            version=version,
            config_dir=self.user_conf.parent,
            running=running,
        )

    def read_state(self) -> TargetState:
        values = self._managed_values()
        try:
            _, data = self._read_user()
        except ValueError:
            return TargetState(values=values, bootstrap="weird")
        shadowed = tuple(k for k in REGISTRY if _lookup(data, k) is not _MISSING)
        if not self._has_include(data):
            status: BootstrapStatus = "none"
        elif shadowed:
            status = "user-shadowed"
        else:
            status = "bootstrapped"
        return TargetState(values=values, bootstrap=status, shadowed_keys=shadowed)

    def validate(self, op: Op) -> list[Problem]:
        problems: list[Problem] = []

        def bad(code: str, message: str) -> None:
            problems.append(Problem(code=code, message=message))

        if isinstance(op, SetOp | FontOp):
            if op.target != self.name:
                bad("wrong-target", f"op targets {op.target}, not alacritty")
            problems += validate_common(
                ChangePlan(version=1, summary="alacritty", ops=[op])
            )
        if isinstance(op, SetOp):
            if op.key not in REGISTRY:
                bad("unknown-key", f"alacritty does not support setting {op.key!r}")
            else:
                problems += REGISTRY[op.key].check(op.key, op.value)
                if not problems and not _value_ok(op.key, op.value):
                    bad("bad-value", f"{op.key}: invalid value")
        elif isinstance(op, FontOp):
            if op.role != "terminal":
                bad("unsupported-role", "alacritty only has a terminal font")
        elif isinstance(op, PaletteOp):
            if self.name not in op.targets:
                bad("wrong-target", "palette op does not include alacritty")
            try:
                load_palette(op.palette, op.variant)
            except PaletteError as exc:
                bad("unknown-palette", str(exc))
        else:
            bad("unsupported-op", "the alacritty adapter does not handle this op")
        if self._managed_ownership() in ("user-shadowed", "weird"):
            bad("unsupported", "buckwheat.toml is not managed by buckwheat")
        return problems

    def _expand_op(self, op: Op) -> dict[str, Scalar]:
        """Registry key/value pairs for an already validated op."""
        if isinstance(op, SetOp):
            return {op.key: op.value}
        if isinstance(op, FontOp):
            out: dict[str, Scalar] = {"font.normal.family": op.family}
            if op.size is not None:
                out["font.size"] = op.size
            return out
        if isinstance(op, PaletteOp):
            palette = load_palette(op.palette, op.variant)
            return {k: _slot_value(palette, s) for k, s in PALETTE_SLOTS.items()}
        raise ValueError("unsupported op")

    def allowed_keys(self) -> list[str]:
        return sorted(REGISTRY)

    def state_updates(
        self, ops: Sequence[Op], state: TargetState
    ) -> Mapping[str, Scalar]:
        """Registry key -> value that ``ops`` write (set, font and palette ops)."""
        updates: dict[str, Scalar] = {}
        for op in ops:  # defence in depth: validate again before emitting anything
            problems = self.validate(op)
            if problems:
                raise ValueError("; ".join(p.message for p in problems))
            updates.update(self._expand_op(op))
        return updates

    def render(self, ops: list[Op], state: TargetState) -> RenderResult:
        updates = self.state_updates(ops, state)
        values = merge_state(_clean(state.values), updates)
        return RenderResult(files={self.managed_conf: format_managed(values)})

    def reload(self) -> ReloadResult:
        """Alacritty live-reloads on file change; just check that it is not off."""
        try:
            _, data = self._read_user()
        except ValueError as exc:
            return ReloadResult(True, f"could not check live_config_reload: {exc}")
        general = data.get("general")
        flag = general.get("live_config_reload") if isinstance(general, dict) else None
        if flag is None:
            flag = data.get("live_config_reload")
        if flag is False:
            return ReloadResult(
                True, "live_config_reload is off in alacritty.toml; restart alacritty"
            )
        return ReloadResult(True, "alacritty reloads its config on change")

    # --- bootstrap -----------------------------------------------------------

    def bootstrap_plan(self) -> BootstrapPlan:
        """The include the user is asked to confirm."""
        return BootstrapPlan(
            file=self.user_conf,
            line=self._import_line(),
            array_entry=toml_str(self._entry()),  # see _append_to_array
        )

    def bootstrap_files(self) -> list[Path]:
        return [self.user_conf]

    def install(self, plan: BootstrapPlan) -> RenderResult:
        if plan.file != self.user_conf or plan.line != self._import_line():
            raise ValueError("bootstrap plan does not match the alacritty include")
        if self._managed_ownership() in ("user-shadowed", "weird"):
            raise ValueError("buckwheat.toml is user-owned; refusing to replace it")
        if self._user_conf_default:
            # An include in a file alacritty does not read has no effect, and a new
            # file in front of the one it reads would hide that one. Refuse both.
            read = _effective_conf()
            if read is not None and read.suffix == ".yml":
                # alacritty reads YAML only when no alacritty.toml exists. Buckwheat
                # neither parses nor writes YAML, so the user migrates it first.
                raise ValueError(
                    f"alacritty reads {read}, a YAML file buckwheat does not edit; "
                    "run `alacritty migrate` to convert it to TOML, then install again"
                )
            if read is not None and read != self.user_conf:
                raise ValueError(
                    f"alacritty reads {read}, not {self.user_conf}; "
                    f"add the include to {read} by hand"
                )
        text, data = self._read_user()
        legacy = (v := self._version()) is not None and v[:2] < GENERAL_IMPORT_SINCE
        new_text = text if self._has_include(data) else self._add_include(text, legacy)
        files = {
            self.managed_conf: format_managed(self._managed_values()),
            self.user_conf: new_text,
        }
        return RenderResult(files=files)

    def uninstall(self) -> RenderResult:
        files: dict[Path, str] = {}
        leftovers: tuple[str, ...] = ()
        text, data = self._read_user()
        if self._has_include(data):
            new_text = self._remove_include(text)  # parses: checked inside
            files[self.user_conf] = new_text
            if self._has_empty_import(tomllib.loads(new_text)):
                leftovers = (
                    f"{self.user_conf.name} is left with an empty import list "
                    "(`import = []`). Buckwheat removed only its own entry, and an "
                    "empty list does nothing, so remove that line by hand if you do "
                    "not need it.",
                )
        deletes: frozenset[Path] = frozenset()
        if self._managed_ownership() == "bootstrapped":
            deletes = frozenset({self.managed_conf})
        return RenderResult(files=files, deletes=deletes, leftovers=leftovers)

    def _has_empty_import(self, data: Mapping[str, Any]) -> bool:
        general = data.get("general")
        lists = [data.get("import")]
        if isinstance(general, dict):
            lists.append(general.get("import"))
        return any(isinstance(entries, list) and not entries for entries in lists)

    def _add_include(self, text: str, legacy: bool) -> str:
        """Return ``text`` with the import entry added, or raise ValueError."""
        data = tomllib.loads(text)
        lines = _split_lines(text)
        nl = "\r\n" if "\r\n" in text else "\n"
        entry = toml_str(self._entry())
        expected = copy.deepcopy(data)
        general = data.get("general")
        in_general = isinstance(general, dict) and "import" in general
        if in_general or "import" in data:
            # An existing simple array: append our entry to it.
            holder = expected["general"] if in_general else expected
            if not isinstance(holder["import"], list):
                raise ValueError("import is not a list; add the include by hand")
            holder["import"].append(self._entry())
            new_lines = self._append_to_array(lines, entry)
        else:
            block = [INCLUDE_BEGIN, self._import_line(), INCLUDE_END]
            if legacy:
                expected["import"] = [self._entry()]
                new_lines = _insert_block(lines, 0, block, nl)
            elif isinstance(general, dict):
                heads = [
                    i
                    for i, line in enumerate(lines)
                    if _GENERAL_HEADER_RE.match(_body(line))
                ]
                if len(heads) != 1:
                    raise ValueError("cannot find [general]; add the include by hand")
                expected["general"]["import"] = [self._entry()]
                new_lines = _insert_block(lines, heads[0] + 1, block, nl)
            elif "general" in data:
                raise ValueError("general is not a table; add the include by hand")
            else:
                expected["general"] = {"import": [self._entry()]}
                block.insert(1, "[general]")
                new_lines = _insert_block(lines, len(lines), block, nl)
        new_text = "".join(new_lines)
        try:
            ok = tomllib.loads(new_text) == expected
        except tomllib.TOMLDecodeError:
            ok = False
        if not ok:
            raise ValueError("cannot add the include safely; add it by hand")
        return new_text

    @staticmethod
    def _append_to_array(lines: list[str], entry: str) -> list[str]:
        hits = [
            (i, m)
            for i, line in enumerate(lines)
            if (m := _IMPORT_LINE_RE.match(_body(line)))
        ]
        if len(hits) != 1:
            raise ValueError("import must be one simple line; add the include by hand")
        i, m = hits[0]
        inner = m[2]
        added = entry if not inner.strip() else f", {entry}"
        out = list(lines)
        out[i] = f"{m[1]}{inner}{added}{m[3]}{out[i][len(_body(out[i])) :]}"
        return out

    def _remove_include(self, text: str) -> str:
        """Undo ``_add_include``; ValueError if the entry cannot be found."""
        lines = _split_lines(text)
        removed = _remove_block(lines)
        if removed is None:
            quoted = toml_str(self._entry())
            removed = list(lines)
            for i, line in enumerate(lines):
                m = _IMPORT_LINE_RE.match(_body(line))
                if m and quoted in m[2]:
                    inner = m[2].replace(f", {quoted}", "", 1)
                    if inner == m[2]:
                        inner = m[2].replace(quoted, "", 1)
                    removed[i] = f"{m[1]}{inner}{m[3]}{line[len(_body(line)) :]}"
                    break
            else:
                raise ValueError("cannot find the include; remove it by hand")
        new_text = "".join(removed)
        try:
            still = self._has_include(tomllib.loads(new_text))
        except tomllib.TOMLDecodeError:
            still = True
        if still:
            raise ValueError("cannot remove the include safely; remove it by hand")
        return new_text


_MISSING = object()


def _lookup(data: Mapping[str, Any], dotted: str) -> object:
    """Value at a dotted path in parsed TOML, or ``_MISSING``."""
    node: object = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


register("alacritty")(AlacrittyAdapter)
