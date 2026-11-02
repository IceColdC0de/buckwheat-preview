"""Adapter protocol and shared helpers.

No concrete adapters live here. This module has the protocol, the data types
adapters exchange with the session, a subprocess runner (plus a fake for
tests), the managed state file, and a name-based registry.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from buckwheat.config import state_dir
from buckwheat.plan.context import Problem
from buckwheat.plan.models import Op, Scalar
from buckwheat.safety.atomic import atomic_write
from buckwheat.safety.bootstrap import INCLUDE_BEGIN as INCLUDE_BEGIN
from buckwheat.safety.bootstrap import INCLUDE_END as INCLUDE_END

MANAGED_HEADER = (
    "# Managed by buckwheat. Edits are overwritten. "
    "Remove with: buckwheat uninstall <target>"
)

DEFAULT_TIMEOUT = 5.0

BootstrapStatus = Literal["none", "bootstrapped", "user-shadowed", "weird"]


# --- key registry ---------------------------------------------------------


@dataclass(frozen=True)
class KeySpec:
    """What one settable key accepts. The registry of these is code, not LLM data."""

    type: type[bool] | type[int] | type[float] | type[str]
    min: float | None = None
    max: float | None = None
    enum: tuple[str, ...] | None = None
    render: Callable[[Scalar], str] = str
    since: str | None = None  # app version gating
    until: str | None = None

    def check(self, key: str, value: Scalar) -> list[Problem]:
        """Return problems with ``value`` for this key (op_index left unset)."""
        # bool is a subclass of int: never let True pass as a number.
        if self.type is bool:
            ok = isinstance(value, bool)
        elif self.type is float:
            ok = isinstance(value, int | float) and not isinstance(value, bool)
        elif self.type is int:
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = isinstance(value, str)
        if not ok:
            return [_problem("bad_type", f"{key}: expected {self.type.__name__}")]
        if self.enum is not None and value not in self.enum:
            return [_problem("bad_enum", f"{key}: must be one of {self.enum}")]
        if isinstance(value, int | float) and not isinstance(value, bool):
            if self.min is not None and value < self.min:
                return [_problem("out_of_range", f"{key}: below minimum {self.min}")]
            if self.max is not None and value > self.max:
                return [_problem("out_of_range", f"{key}: above maximum {self.max}")]
        return []


def _problem(code: str, message: str) -> Problem:
    return Problem(code=code, message=message)


# --- data exchanged with the session -------------------------------------


@dataclass(frozen=True)
class Detection:
    installed: bool
    version: str | None = None
    config_dir: Path | None = None
    running: bool = False
    # Which config format is in use, for reports ("Lua config (hyprland.lua)").
    config_kind: str | None = None


@dataclass(frozen=True)
class TargetState:
    values: Mapping[str, Scalar] = field(default_factory=dict)
    bootstrap: BootstrapStatus = "none"
    shadowed_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderResult:
    """What the caller should do to disk. Adapters never write files.

    ``files`` maps a path to its full new contents. ``deletes`` lists paths to
    remove (used by ``uninstall`` for buckwheat's own managed files). A path
    may not be in both. Both go through the same path allowlist.

    ``leftovers`` (set by ``uninstall`` only) are lines the user must remove by
    hand: include lines buckwheat did not remove, with the reason. They are
    messages for the user, not edits.
    """

    files: Mapping[Path, str] = field(default_factory=dict)
    deletes: frozenset[Path] = frozenset()
    leftovers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        both = set(self.files) & set(self.deletes)
        if both:
            shown = ", ".join(sorted(str(p) for p in both))
            raise ValueError(f"path both written and deleted: {shown}")


@dataclass(frozen=True)
class ReloadResult:
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class BootstrapPlan:
    """The one-line include the user must confirm.

    ``array_entry`` is set only by alacritty: the quoted entry it may append
    to an existing one-line ``import = [...]`` outside a marker block.
    """

    file: Path
    line: str
    array_entry: str | None = None


class Adapter(Protocol):
    name: ClassVar[str]  # a TargetName, or "wallpaper" (not a plan target)
    managed_files: ClassVar[list[Path]]  # the ONLY files this adapter may write

    def detect(self) -> Detection: ...
    def read_state(self) -> TargetState: ...
    def allowed_keys(self) -> list[str]: ...
    def validate(self, op: Op) -> list[Problem]: ...
    def state_updates(
        self, ops: Sequence[Op], state: TargetState
    ) -> Mapping[str, Scalar]: ...
    def render(self, ops: list[Op], state: TargetState) -> RenderResult: ...
    def reload(self) -> ReloadResult: ...
    def bootstrap_plan(self) -> BootstrapPlan | None: ...  # None: nothing to include
    def bootstrap_files(self) -> list[Path]: ...  # user files install/uninstall edit
    def install(self, plan: BootstrapPlan) -> RenderResult: ...
    def uninstall(self) -> RenderResult: ...


def is_managed_file(path: Path) -> bool:
    """True if ``path`` exists and its first line carries buckwheat's header.

    Works for every comment style the adapters use (``#``, ``//``, ``/* */``,
    Lua's ``--``).
    Used before deleting a file so a user-owned file is never removed.
    """
    try:
        with path.open(encoding="utf-8", newline="") as f:
            first = f.readline()
    except (OSError, UnicodeDecodeError):
        return False
    return first.lstrip("#/*- ").startswith("Managed by buckwheat.")


# --- command runner -------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandError(Exception):
    """The command could not be run (missing binary, timeout)."""


class CommandRunner(Protocol):
    def run(
        self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT
    ) -> CommandResult: ...

    def spawn(self, argv: Sequence[str]) -> None: ...


class SubprocessRunner:
    """Runs argv lists without a shell. Argv must come from constants."""

    def run(
        self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT
    ) -> CommandResult:
        if not argv:
            raise CommandError("empty argv")
        try:
            proc = subprocess.run(
                list(argv),
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CommandError(f"{argv[0]}: {exc}") from exc
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    def spawn(self, argv: Sequence[str]) -> None:
        if not argv:
            raise CommandError("empty argv")
        try:
            subprocess.Popen(
                list(argv),
                shell=False,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise CommandError(f"{argv[0]}: {exc}") from exc


class FakeRunner:
    """Test double: records calls and returns canned results by argv."""

    def __init__(
        self,
        results: Mapping[tuple[str, ...], CommandResult | CommandError] | None = None,
        default: CommandResult | None = None,
    ) -> None:
        self.results = dict(results or {})
        self.default = default or CommandResult(0)
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.spawned: list[tuple[str, ...]] = []

    def run(
        self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT
    ) -> CommandResult:
        key = tuple(argv)
        self.calls.append((key, timeout))
        result = self.results.get(key, self.default)
        if isinstance(result, CommandError):
            raise result
        return result

    def spawn(self, argv: Sequence[str]) -> None:
        if not argv:
            raise CommandError("empty argv")
        key = tuple(argv)
        self.spawned.append(key)
        result = self.results.get(key)
        if isinstance(result, CommandError):
            raise result

    @property
    def spawn_calls(self) -> list[tuple[str, ...]]:
        return self.spawned


# --- managed state file ---------------------------------------------------

ManagedState = dict[str, dict[str, Scalar]]  # target -> key -> value


def managed_state_path() -> Path:
    return state_dir() / "managed.json"


def load_managed_state(path: Path | None = None) -> ManagedState:
    """Read the state file; a missing or unreadable file is empty state."""
    path = path or managed_state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: ManagedState = {}
    for target, values in raw.items():
        if isinstance(target, str) and isinstance(values, dict):
            state[target] = {
                k: v
                for k, v in values.items()
                if isinstance(k, str) and isinstance(v, bool | int | float | str)
            }
    return state


def save_managed_state(state: ManagedState, path: Path | None = None) -> Path:
    """Atomically write the state file (the path itself is the only allowed one)."""
    path = path or managed_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    return atomic_write(path, text, [path])


def merge_state(
    old: Mapping[str, Scalar], updates: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    """New state = old state overlaid with updates (render determinism)."""
    return {**old, **updates}


# --- registry -------------------------------------------------------------

_REGISTRY: dict[str, type[Adapter]] = {}


def register(name: str) -> Callable[[type[Adapter]], type[Adapter]]:
    """Class decorator: register an adapter class under `name`."""

    def deco(cls: type[Adapter]) -> type[Adapter]:
        if name in _REGISTRY:
            raise ValueError(f"adapter already registered: {name}")
        _REGISTRY[name] = cls
        return cls

    return deco


def available() -> list[str]:
    return sorted(_REGISTRY)


def get(name: str) -> type[Adapter]:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(available()) or "none"
        raise KeyError(f"unknown adapter {name!r} (known: {known})") from None
