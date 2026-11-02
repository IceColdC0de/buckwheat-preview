"""``buckwheat check``: read-only diagnostics for a real desktop.

It shows what buckwheat would detect and use, before any change is made. It
never writes a file of its own and never changes a running program:

- adapters are only asked to ``detect`` and to ``read_state``;
- the provider is built without a network call, and its API-key variable is
  only checked for being set (the value is never read out or printed);
- the provider's SDK is only imported, to see that it works;
- ``hyprctl configerrors`` runs only with ``hyprland_errors``; it is a query.

Exit code 0 means everything that is set up looks usable; 1 means something is
broken or missing. A target that is not installed is reported but is not a
problem, unless none of the targets is installed.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from buckwheat.adapters.alacritty import AlacrittyAdapter
from buckwheat.adapters.base import (
    Adapter,
    CommandError,
    CommandRunner,
    Detection,
    SubprocessRunner,
    load_managed_state,
)
from buckwheat.adapters.hyprland import HyprlandAdapter
from buckwheat.adapters.kitty import KittyAdapter
from buckwheat.adapters.wallpaper import WallpaperAdapter
from buckwheat.adapters.waybar import WaybarAdapter
from buckwheat.config import (
    ConfigError,
    ProviderSettings,
    Settings,
    default_config_path,
    load_settings,
)
from buckwheat.plan.models import Scalar
from buckwheat.providers import ProviderError, create
from buckwheat.providers.anthropic import DEFAULT_ENV as ANTHROPIC_DEFAULT_ENV

# The SDK package each provider needs. It is imported only to see that it works.
SDK_PACKAGES: dict[str, str] = {"anthropic": "anthropic", "openai_compat": "openai"}


@dataclass(frozen=True)
class _Target:
    name: str
    adapter: Adapter
    # (role, path) pairs, read when the report runs; None means "no such file".
    files: Callable[[], Sequence[tuple[str, Path | None]]]
    # Why buckwheat cannot manage this target at all, or None if it can.
    unsupported: Callable[[], str | None] = lambda: None


def _targets(runner: CommandRunner) -> list[_Target]:
    """Every registered adapter, built like the session's defaults but with ``runner``.

    Paths are resolved when this runs, so HOME and XDG_* are read at call time.
    A test fails if the registry gains an adapter that is not listed here.
    """
    alacritty = AlacrittyAdapter(runner)
    hyprland = HyprlandAdapter(runner)
    kitty = KittyAdapter(runner)
    wallpaper = WallpaperAdapter(runner)
    waybar = WaybarAdapter(runner=runner)

    def waybar_files() -> list[tuple[str, Path | None]]:
        plan = waybar.bootstrap_plan()
        return [
            ("user", plan.file if plan is not None else None),
            ("user", waybar.style_path),
            ("managed", waybar.json_path),
            ("managed", waybar.css_path),
        ]

    def hyprland_files() -> list[tuple[str, Path | None]]:
        # Hyprland 0.57+ reads hyprland.lua and buckwheat.lua instead.
        if hyprland.uses_lua():
            return [("user", hyprland.lua_path), ("managed", hyprland.lua_managed_path)]
        return [("user", hyprland.main_path), ("managed", hyprland.managed_path)]

    return [
        _Target(
            "alacritty",
            alacritty,
            lambda: [
                ("user", alacritty.user_conf),
                ("managed", alacritty.managed_conf),
            ],
        ),
        _Target(
            "hyprland",
            hyprland,
            hyprland_files,
            hyprland.unsupported_reason,
        ),
        _Target(
            "kitty",
            kitty,
            lambda: [("user", kitty.user_file), ("managed", kitty.managed_file)],
        ),
        _Target("wallpaper", wallpaper, lambda: [("managed", wallpaper.conf_path)]),
        _Target("waybar", waybar, waybar_files),
    ]


def run_check(
    config_path: str | None = None,
    *,
    hyprland_errors: bool = False,
    runner: CommandRunner | None = None,
) -> int:
    """Print the read-only report; return 0 when usable, 1 when not."""
    runner = runner if runner is not None else SubprocessRunner()
    problems: list[str] = []
    print("buckwheat check: read-only, nothing is written")
    print()
    settings = _report_config(config_path, problems)
    _report_provider(settings, problems)
    print()
    print("targets")
    try:
        managed = load_managed_state()
    except OSError as exc:
        managed = {}
        problems.append(f"managed state file cannot be read ({type(exc).__name__})")
    targets = _targets(runner)
    absent: list[str] = []
    for target in targets:
        if not _report_target(target, managed, problems):
            absent.append(target.name)
        print()
    if hyprland_errors:
        _report_configerrors(runner, problems)
        print()
    if len(absent) == len(targets):
        problems.append("none of the targets is installed")
    return _summary(problems, absent)


def _report_config(config_path: str | None, problems: list[str]) -> Settings | None:
    path = Path(config_path) if config_path is not None else default_config_path()
    if path.exists():
        note = "found"
    elif config_path is None:
        note = "not found; defaults apply"
    else:
        note = "not found"
    print(f"config: {path} ({note})")
    try:
        return load_settings(config_path)
    except ConfigError as exc:
        print(f"  error: {exc}")
        problems.append(str(exc))
        return None


def _report_provider(settings: Settings | None, problems: list[str]) -> None:
    if settings is None:
        print("provider: unknown (the config could not be loaded)")
        return
    name = settings.provider
    table = settings.providers.get(name)
    print(f"provider: {name}")
    try:
        create(name, table.model_dump() if table is not None else {})
    except ProviderError as exc:
        print(f"  config: {exc}")
        problems.append(str(exc))
    env = _key_env(name, table)
    if env:
        is_set = bool(os.environ.get(env))
        print(f"  api key: {env} is {'set' if is_set else 'not set'}")
        if not is_set:
            problems.append(f"{env} is not set")
    else:
        print("  api key: no environment variable configured")
    sdk = SDK_PACKAGES[name]
    try:
        importlib.import_module(sdk)
    except Exception as exc:  # any failure here means the provider cannot run
        print(f"  sdk: {sdk} cannot be imported ({type(exc).__name__})")
        problems.append(f"the {sdk} package cannot be imported")
    else:
        print(f"  sdk: {sdk} can be imported")


def _key_env(name: str, table: ProviderSettings | None) -> str:
    """Name of the environment variable holding the key; "" if none is used."""
    configured = table.api_key_env if table is not None else ""
    if name == "anthropic":
        return configured or ANTHROPIC_DEFAULT_ENV
    return configured


def _report_target(
    target: _Target,
    managed: Mapping[str, Mapping[str, Scalar]],
    problems: list[str],
) -> bool:
    """Print one target's facts; return whether it is installed."""
    print(target.name)
    try:
        detection = target.adapter.detect()
        state = target.adapter.read_state()
        # The same merge as Session._state: the managed state file wins.
        values = {**state.values, **managed.get(target.name, {})}
        file_lines = [_file_line(role, path) for role, path in target.files()]
        unsupported = target.unsupported()
    except Exception as exc:  # one unreadable target must not hide the others
        print(f"  could not check: {type(exc).__name__}: {exc}")
        problems.append(f"{target.name}: could not be checked ({type(exc).__name__})")
        return False
    print(f"  detected: {_detected_text(detection)}")
    if detection.config_kind is not None:
        print(f"  config: {detection.config_kind}")
    for line in file_lines:
        print(f"  {line}")
    if unsupported is not None:
        print(f"  not supported: {unsupported}")
    bootstrapped = "yes" if state.bootstrap == "bootstrapped" else "no"
    print(f"  bootstrapped: {bootstrapped} (status: {state.bootstrap})")
    if values:
        print("  managed values:")
        for key in sorted(values):
            print(f"    {key} = {values[key]}")
    else:
        print("  managed values: none")
    shadowed = ", ".join(state.shadowed_keys)
    print(f"  shadowed keys: {shadowed or 'none'}")
    if detection.installed:
        if unsupported is not None:
            problems.append(f"{target.name}: {unsupported}")
        if state.bootstrap == "weird":
            problems.append(
                f"{target.name}: config is in a state buckwheat cannot manage "
                "(status: weird)"
            )
        if shadowed:
            problems.append(f"{target.name}: your own config overrides {shadowed}")
    return detection.installed


def _detected_text(detection: Detection) -> str:
    if not detection.installed:
        return "no"
    notes: list[str] = []
    if detection.version:
        notes.append(f"version {detection.version}")
    if detection.running:
        notes.append("running")
    return "yes" + (f" ({', '.join(notes)})" if notes else "")


def _file_line(role: str, path: Path | None) -> str:
    if path is None:
        return f"{role} file: none found"
    if path.is_symlink():
        where = f"symlink to {path.resolve()}"
    elif path.exists():
        where = "exists"
    else:
        where = "missing"
    return f"{role} file: {path} ({where})"


def _report_configerrors(runner: CommandRunner, problems: list[str]) -> None:
    """Run ``hyprctl configerrors`` and print its output exactly as returned."""
    print("hyprctl configerrors")
    try:
        result = runner.run(["hyprctl", "configerrors"])
    except CommandError as exc:
        print(f"  could not run: {exc}")
        problems.append(f"hyprctl configerrors could not run: {exc}")
        return
    print(f"  exit code: {result.returncode}")
    _print_verbatim("stdout", result.stdout)
    if result.stderr:
        _print_verbatim("stderr", result.stderr)
    # The rule the Hyprland adapter applies after a reload: empty or "no errors".
    text = result.stdout.strip()
    if result.returncode != 0 or (text and text.lower() != "no errors"):
        problems.append("hyprctl configerrors reports errors or failed (see above)")


def _print_verbatim(label: str, text: str) -> None:
    print(f"--- {label} ---")
    if text:
        print(text, end="" if text.endswith("\n") else "\n")
    else:
        print("(empty)")
    print(f"--- end {label} ---")


def _summary(problems: list[str], absent: list[str]) -> int:
    if absent:
        print(f"not installed (reported, not a problem): {', '.join(absent)}")
    if not problems:
        print("result: everything looks usable")
        return 0
    print(f"result: {len(problems)} problem(s) found")
    for problem in problems:
        print(f"  - {problem}")
    return 1
