"""Command-line entry point logic."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, ClassVar

from buckwheat import __version__
from buckwheat.wording import LEFT_BEHIND_HEADER, partly_removed

if TYPE_CHECKING:
    from buckwheat.config import Settings
    from buckwheat.plan import ChangePlan, PlanContext
    from buckwheat.session import Session


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buckwheat",
        description="Customise your Linux desktop with plain-text requests.",
    )
    parser.add_argument(
        "--version", action="store_true", help="print the version and exit"
    )
    parser.add_argument("--config", help="path to a config file (default: XDG config)")
    commands = parser.add_subparsers(dest="command")
    check = commands.add_parser(
        "check",
        help="read-only diagnostics for this desktop (writes nothing)",
        description=(
            "Show what buckwheat would detect and use, without changing anything. "
            "Exits 0 if everything set up looks usable, 1 otherwise."
        ),
    )
    check.add_argument(
        "--hyprland-errors",
        action="store_true",
        help="also run 'hyprctl configerrors' and print its raw output",
    )
    # SUPPRESS: a --config given before 'check' must not be reset to None here.
    check.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="path to a config file (default: XDG config)",
    )
    un = commands.add_parser(
        "uninstall",
        help="remove buckwheat's include lines and managed files",
        description=(
            "Remove the include block buckwheat added to your config files and "
            "delete the files it manages. Shows the changes and asks first."
        ),
    )
    un.add_argument(
        "targets", nargs="*", metavar="target", help="targets to remove (default: all)"
    )
    un.add_argument("--yes", "-y", action="store_true", help="do not ask to confirm")
    un.add_argument("--config", default=argparse.SUPPRESS, help="path to a config file")
    return parser


def run(argv: Sequence[str] | None = None, *, ask: Callable[[str], str] = input) -> int:
    """Run the CLI and return a process exit code.

    ``--version`` prints the version; ``check`` prints the read-only diagnostics;
    ``uninstall`` removes buckwheat; with no arguments the TUI starts. ``ask``
    reads the y/N answer (tests replace it).
    """
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f"buckwheat {__version__}")
        return 0
    if args.command == "check":
        # Imported here for the same reason as launch_tui: keep --version light.
        from buckwheat.check import run_check

        return run_check(args.config, hyprland_errors=args.hyprland_errors)
    if args.command == "uninstall":
        return uninstall(args.config, args.targets, yes=args.yes, ask=ask)
    return launch_tui(args.config)


class _NoPlanner:
    """Provider for commands that never plan (no API key needed)."""

    name: ClassVar[str] = "none"

    def plan(self, request: str, context: PlanContext) -> ChangePlan:
        raise RuntimeError("this command does not make plans")


def make_session(settings: Settings) -> Session:
    """A session with every adapter and no provider (tests replace this)."""
    from buckwheat.session import Session

    return Session(_NoPlanner(), settings)


def uninstall(
    config_path: str | None,
    targets: Sequence[str],
    *,
    yes: bool = False,
    ask: Callable[[str], str] = input,
) -> int:
    """``buckwheat uninstall [target ...]``: preview, confirm, remove.

    Exit codes: 0 done (or nothing to do), 1 declined or failed, 2 bad input.
    """
    from buckwheat.config import ConfigError, load_settings
    from buckwheat.session import SessionError

    try:
        settings = load_settings(config_path)
    except ConfigError as exc:
        print(f"buckwheat: {exc}", file=sys.stderr)
        return 2
    session = make_session(settings)
    unknown = [t for t in targets if t not in session.adapters]
    if unknown:
        known = ", ".join(sorted(session.adapters))
        print(
            f"buckwheat: unknown target {unknown[0]!r} (known: {known})",
            file=sys.stderr,
        )
        return 2
    try:
        proposal = session.propose_uninstall(targets)
    except SessionError as exc:
        print(f"buckwheat: {exc}", file=sys.stderr)
        return 1
    if proposal.instructions:
        print('bootstrap = "manual": buckwheat will not edit your files.\n')
        print(proposal.instructions + "\n")
    if proposal.empty:
        print("Nothing for buckwheat to remove.")
        _print_left_behind(proposal.left_behind)
        return 0
    print(proposal.diff, end="" if proposal.diff.endswith("\n") else "\n")
    if not yes:
        names = ", ".join(proposal.adapters)
        try:
            answer = ask(f"Remove buckwheat from {names}? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing changed.")
            return 1
    try:
        change = session.apply_bootstrap(proposal)
    except SessionError as exc:
        print(f"buckwheat: {exc}", file=sys.stderr)
        return 1
    if change.state == "reverted":
        print("Reload failed, so the change was undone:", file=sys.stderr)
        for message in change.messages:
            print(f"  {message}", file=sys.stderr)
        return 1
    session.keep(change.id)
    if proposal.left_behind:
        # Never a plain "Removed." while something is still in the user's files.
        print(partly_removed(change.id))
        _print_left_behind(proposal.left_behind)
    else:
        print(f"Removed. A backup is kept as snapshot {change.id}.")
    return 0


def _print_left_behind(lines: Sequence[str]) -> None:
    """List what uninstall could not remove; the user must delete it by hand."""
    if not lines:
        return
    print(LEFT_BEHIND_HEADER)
    for line in lines:
        print(f"  {line}")


def launch_tui(config_path: str | None = None) -> int:
    # Imported here so --version does not load Textual or the adapters.
    from buckwheat.config import ConfigError, load_settings
    from buckwheat.providers import ProviderError, create
    from buckwheat.session import Session
    from buckwheat.tui.app import BuckwheatApp

    try:
        settings = load_settings(config_path)
        table = settings.providers.get(settings.provider)
        provider = create(settings.provider, table.model_dump() if table else {})
    except (ConfigError, ProviderError) as exc:
        print(f"buckwheat: {exc}", file=sys.stderr)
        return 2
    BuckwheatApp(Session(provider, settings)).run()
    return 0
