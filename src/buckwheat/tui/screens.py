"""Screens for the buckwheat TUI.

These only call ``Session``; all planning, validation and file handling lives
there. Anything slow (provider calls, writes, reloads) runs off the UI thread.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Input, Label, OptionList, Static

from buckwheat.plan import FontOp, Op, PaletteOp, SetOp, WallpaperOp
from buckwheat.providers.base import ProviderError
from buckwheat.safety.snapshot import ModifiedSinceError, SnapshotError, SnapshotInfo
from buckwheat.session import (
    AppliedChange,
    ApplyError,
    BootstrapProposal,
    PlanRejectedError,
    Proposal,
    Session,
    SessionError,
    StaleProposalError,
)
from buckwheat.tui.theme import (
    BANNER_COMPACT,
    GROAT_GOLD,
    HEARTH_RED,
    MUTED_GRAIN,
    SPROUT_SAGE,
    TAGLINE,
)
from buckwheat.wording import LEFT_BEHIND_HEADER, partly_removed

TICK_SECONDS = 0.1


def button_row(*buttons: Button) -> Horizontal:
    return Horizontal(*buttons, classes="button-row")


def describe_op(op: Op) -> str:
    """One plain-language line for an op."""
    if isinstance(op, SetOp):
        return f"Set {op.key} to {op.value} in {op.target}"
    if isinstance(op, PaletteOp):
        name = op.palette + (f" ({op.variant})" if op.variant else "")
        return f"Apply the {name} colour palette to {', '.join(op.targets)}"
    if isinstance(op, FontOp):
        size = f" at size {op.size:g}" if op.size is not None else ""
        return f"Use the font {op.family}{size} for the {op.role} in {op.target}"
    if isinstance(op, WallpaperOp):
        return f"Set the wallpaper to {op.path} ({op.mode})"
    raise AssertionError(f"unknown op: {op!r}")  # pragma: no cover


def setup_notice(proposal: Proposal) -> str:
    """The line at the top of a preview that also sets targets up."""
    names = ", ".join(proposal.setup)
    where = ", ".join(proposal.setup_files) or "their main config"
    return (
        f"Set up and apply: {names} will be set up first. This adds a marked "
        f"buckwheat include block to {where}. Reverting undoes both."
    )


def not_showing_notice(names: Sequence[str]) -> str:
    """Result line when a written change cannot show in some programs."""
    return (
        f"{', '.join(names)} won't show this change: its config does not "
        "include buckwheat's file. Set it up, or see the user guide."
    )


def render_diff(diff: str) -> Text:
    """Colour a unified diff. Built as Text so file contents are never markup."""
    out = Text(no_wrap=False)
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            style = f"bold {GROAT_GOLD}"
        elif line.startswith("+"):
            style = SPROUT_SAGE
        elif line.startswith("-"):
            style = HEARTH_RED
        else:
            style = MUTED_GRAIN
        out.append(line + "\n", style=style)
    return out


class RequestScreen(Screen[None]):
    """Ask what the user wants changed."""

    BINDINGS = [
        Binding("f2", "history", "History"),
        Binding("f3", "uninstall", "Uninstall"),
    ]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with Vertical(id="header-container"):
            yield Static(BANNER_COMPACT, id="brand-emblem")
            yield Static(TAGLINE, id="brand-tagline")
        with Vertical(id="prompt-card"):
            yield Label("What would you like to change?", id="prompt-label")
            yield Input(
                placeholder="e.g. make it a warm gruvbox look with bigger gaps",
                id="request-input",
            )
            yield Static("", id="request-status")
        yield button_row(
            Button("History (F2)", id="open-history"),
            Button("Uninstall (F3)", id="open-uninstall"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        self.query_one(Input).disabled = True
        self.set_status("Thinking...")
        self.propose(text)

    def set_status(self, text: str) -> None:
        self.query_one("#request-status", Static).update(Text(text, style=MUTED_GRAIN))

    @work(exclusive=True)
    async def propose(self, text: str) -> None:
        error: str | list[str] | None = None
        proposal: Proposal | None = None
        try:
            proposal = await asyncio.to_thread(self.session.propose, text)
        except PlanRejectedError as exc:
            error = [f"{_where(p.op_index)}{p.message}" for p in exc.problems]
        except (ProviderError, SessionError) as exc:
            error = str(exc)
        self.query_one(Input).disabled = False
        self.query_one(Input).focus()
        self.set_status("")
        if proposal is not None:
            self.query_one(Input).value = ""
            self.app.push_screen(PreviewScreen(self.session, proposal))
        elif isinstance(error, list):
            self.app.push_screen(
                ErrorScreen("That plan was rejected, nothing was changed.", error)
            )
        else:
            self.app.push_screen(
                ErrorScreen("The request failed, nothing was changed.", [str(error)])
            )

    def action_history(self) -> None:
        self.app.push_screen(HistoryScreen(self.session))

    def action_uninstall(self) -> None:
        self.app.push_screen(UninstallScreen(self.session))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "open-history":
            self.action_history()
        elif event.button.id == "open-uninstall":
            self.action_uninstall()


def _where(op_index: int | None) -> str:
    return "" if op_index is None else f"Op {op_index + 1}: "


class ErrorScreen(ModalScreen[None]):
    """Why something was refused or failed. Nothing was written."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    def __init__(self, headline: str, reasons: list[str]) -> None:
        super().__init__()
        self.headline = headline
        self.reasons = reasons

    def compose(self) -> ComposeResult:
        with Vertical(id="error-card"):
            yield Static(
                Text(self.headline, style=f"bold {HEARTH_RED}"), id="error-head"
            )
            for reason in self.reasons:
                yield Static(Text("- " + reason), classes="error-reason")
            yield button_row(Button("Close", id="close", classes="primary"))

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class LeftBehindScreen(ModalScreen[None]):
    """A kept uninstall that removed what it could. Lists what is left by hand."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    def __init__(self, headline: str, lines: tuple[str, ...]) -> None:
        super().__init__()
        self.headline = headline
        self.lines = lines

    def compose(self) -> ComposeResult:
        with Vertical(id="error-card"):
            yield Static(
                Text(self.headline, style=f"bold {GROAT_GOLD}"), id="left-behind-head"
            )
            yield Static(Text(LEFT_BEHIND_HEADER), classes="op-line")
            for line in self.lines:
                yield Static(Text("  " + line), classes="error-reason")
            yield button_row(Button("Close", id="close", classes="primary"))

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class PreviewScreen(Screen[None]):
    """Diff preview. Nothing is written until the user confirms.

    If the change touches targets that do not read buckwheat's file yet, the
    preview also shows their one-time setup (the marked include block) and
    Enter does both as one change: one snapshot, one keep-or-revert countdown.
    """

    BINDINGS = [
        Binding("enter,y", "confirm", "Apply"),
        Binding("escape,n", "cancel", "Cancel"),
    ]

    def __init__(self, session: Session, proposal: Proposal) -> None:
        super().__init__()
        self.session = session
        self.proposal = proposal
        self._busy = False

    def compose(self) -> ComposeResult:
        proposal = self.proposal
        plan = proposal.plan
        with VerticalScroll(id="diff-card"):
            if proposal.setup:
                yield Static(
                    Text(setup_notice(proposal), style=f"bold {GROAT_GOLD}"),
                    classes="op-line",
                    id="setup-notice",
                )
            yield Static(Text(plan.summary), classes="diff-header", id="plan-summary")
            for op in plan.ops:
                yield Static(Text("* " + describe_op(op)), classes="op-line")
            for reason in proposal.skipped:
                yield Static(
                    Text("! skipped " + reason, style=HEARTH_RED), classes="op-warning"
                )
            for warning in proposal.warnings:
                yield Static(
                    Text("! " + warning, style=HEARTH_RED), classes="op-warning"
                )
            if proposal.instructions:
                yield Static(
                    Text(proposal.instructions),
                    classes="op-line",
                    id="setup-instructions",
                )
            if proposal.empty:
                yield Static(
                    Text("Nothing can be applied yet, so nothing will change."),
                    classes="op-line",
                    id="preview-empty",
                )
            if proposal.setup_diff:
                yield Static(
                    Text("Setup:", style=f"bold {GROAT_GOLD}"), classes="op-line"
                )
                yield Static(render_diff(proposal.setup_diff), id="setup-diff")
                yield Static(
                    Text("Your change:", style=f"bold {GROAT_GOLD}"), classes="op-line"
                )
            yield Static(render_diff(proposal.diff), id="diff-body")
        buttons = [Button("Cancel (Esc)", id="cancel")]
        if not proposal.empty:
            label = "Set up and apply (Enter)" if proposal.setup else "Apply (Enter)"
            buttons.insert(0, Button(label, id="confirm", classes="primary"))
        yield button_row(*buttons)
        yield Footer()

    def action_cancel(self) -> None:
        if not self._busy:
            self.app.pop_screen()

    def action_confirm(self) -> None:
        if self._busy or self.proposal.empty:
            return
        self._busy = True
        self.apply()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.action_confirm()
        else:
            self.action_cancel()

    @work(exclusive=True)
    async def apply(self) -> None:
        try:
            change = await asyncio.to_thread(self.session.apply, self.proposal)
        except StaleProposalError:
            await self._fail("Files changed since the preview. Please ask again.")
        except (ApplyError, SessionError, OSError) as exc:
            await self._fail(str(exc))
        else:
            if change.state == "reverted":
                reasons = list(change.messages) or ["a reload failed"]
                await self._fail(
                    "A reload failed, so your files were put back as they were.",
                    reasons,
                )
            else:
                self.app.switch_screen(CountdownScreen(self.session, change))

    async def _fail(self, headline: str, reasons: list[str] | None = None) -> None:
        self.app.pop_screen()
        await self.app.push_screen(ErrorScreen(headline, reasons or []))


class CountdownScreen(Screen[None]):
    """Keep-or-revert. Timing out reverts."""

    BINDINGS = [
        Binding("enter,k", "keep", "Keep"),
        Binding("escape,r", "revert", "Revert"),
    ]

    def __init__(
        self,
        session: Session,
        change: AppliedChange,
        on_finish: Callable[[bool], None] | None = None,
        left_behind: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.session = session
        self.change = change
        self.on_finish = on_finish  # called with True if the change was kept
        # What the user must still remove by hand (uninstall only), as the CLI lists it.
        self.left_behind = left_behind
        self.total = float(session.settings.safety.revert_timeout_seconds)
        self._started = time.monotonic()
        self._done = False

    def compose(self) -> ComposeResult:
        with Vertical(id="revert-bar"):
            yield Static("", id="revert-timer")
        yield Static(
            Text(
                "The change is live. Keep it if it looks right; otherwise it is "
                "undone automatically."
            ),
            id="revert-help",
        )
        if self.change.not_showing:
            yield Static(
                Text(not_showing_notice(self.change.not_showing), style=HEARTH_RED),
                classes="op-warning",
                id="not-showing",
            )
        yield button_row(
            Button("Keep (Enter / k)", id="keep", classes="primary"),
            Button("Revert (Esc / r)", id="revert", classes="danger"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self._show(self.total)
        self.set_interval(TICK_SECONDS, self._tick)

    def remaining(self) -> float:
        return max(0.0, self.total - (time.monotonic() - self._started))

    def _show(self, remaining: float) -> None:
        seconds = int(remaining) + (1 if remaining % 1 else 0)
        self.query_one("#revert-timer", Static).update(
            f"Reverting in {seconds} s unless you keep it"
        )

    def _tick(self) -> None:
        if self._done:
            return
        left = self.remaining()
        self._show(left)
        if left <= 0:
            self._finish(keep=False, reason="Time ran out, so the change was undone.")

    def action_keep(self) -> None:
        self._finish(keep=True, reason="")

    def action_revert(self) -> None:
        self._finish(keep=False, reason="Change undone.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "keep":
            self.action_keep()
        else:
            self.action_revert()

    def _finish(self, *, keep: bool, reason: str) -> None:
        if self._done:
            return
        self._done = True
        self.run_finish(keep, reason)

    @work(exclusive=True)
    async def run_finish(self, keep: bool, reason: str) -> None:
        problems: list[str] = []
        if keep:
            try:
                await asyncio.to_thread(self.session.keep, self.change.id)
            except (SessionError, SnapshotError) as exc:
                problems.append(f"Could not record the change as kept: {exc}")
            else:
                if not self.change.not_showing:
                    self.app.notify("Kept. You can roll it back from History (F2).")
        else:
            # force: the user (or the timer) asked to undo, and the files were
            # written seconds ago, so a revert must not be blocked.
            try:
                result = await asyncio.to_thread(
                    self.session.revert, self.change.id, True
                )
            except (SessionError, SnapshotError) as exc:
                problems.append(f"Could not undo the change: {exc}")
            else:
                problems.extend(result.messages)
                if not problems:
                    self.app.notify(reason)
        self.app.pop_screen()
        if self.on_finish is not None:
            self.on_finish(keep and not problems)
        if problems:
            await self.app.push_screen(
                ErrorScreen("Something went wrong afterwards.", problems)
            )
        elif keep and self.change.not_showing:
            # Never a plain "Kept." while some program cannot see the change.
            await self.app.push_screen(
                ErrorScreen(
                    "Kept, but not every program will show it.",
                    [not_showing_notice(self.change.not_showing)],
                )
            )
        elif keep and self.left_behind:
            # Same result the CLI prints after a kept uninstall.
            await self.app.push_screen(
                LeftBehindScreen(partly_removed(self.change.id), self.left_behind)
            )


class BootstrapScreen(Screen[None]):
    """Preview of an install or uninstall.

    Nothing is written until the user confirms. In manual mode the exact lines
    to paste or remove are shown instead; buckwheat then only writes its own
    files (an uninstall that has nothing left to edit by hand).
    """

    BINDINGS = [
        Binding("enter,y", "confirm", "Apply"),
        Binding("escape,n", "cancel", "Close"),
    ]

    def __init__(
        self,
        session: Session,
        proposal: BootstrapProposal,
    ) -> None:
        super().__init__()
        self.session = session
        self.proposal = proposal
        self._busy = False

    @property
    def can_apply(self) -> bool:
        return not self.proposal.empty

    def compose(self) -> ComposeResult:
        proposal = self.proposal
        title = "Set up buckwheat" if proposal.action == "install" else "Uninstall"
        with VerticalScroll(id="diff-card"):
            yield Static(
                Text(title, style=f"bold {GROAT_GOLD}"),
                classes="diff-header",
                id="bootstrap-title",
            )
            if proposal.manual:
                yield Static(
                    Text('bootstrap = "manual": buckwheat will not edit your files.'),
                    classes="op-line",
                    id="bootstrap-manual",
                )
            if proposal.instructions:
                yield Static(
                    Text(proposal.instructions),
                    classes="op-line",
                    id="bootstrap-instructions",
                )
            # Leftovers come only from uninstall, so this is empty for install.
            for leftover in proposal.leftovers:
                yield Static(
                    Text("! remove by hand: " + leftover, style=HEARTH_RED),
                    classes="op-warning",
                )
            for reason in proposal.skipped:
                yield Static(
                    Text("! skipped " + reason, style=HEARTH_RED), classes="op-warning"
                )
            if proposal.empty and not proposal.instructions:
                yield Static(
                    Text("Nothing to do."), classes="op-line", id="bootstrap-empty"
                )
            if proposal.diff:
                yield Static(render_diff(proposal.diff), id="diff-body")
        buttons = [Button("Close (Esc)", id="cancel")]
        if self.can_apply:
            buttons.insert(0, Button("Apply (Enter)", id="confirm", classes="primary"))
        yield button_row(*buttons)
        yield Footer()

    def action_cancel(self) -> None:
        if not self._busy:
            self.app.pop_screen()

    def action_confirm(self) -> None:
        if self._busy or not self.can_apply:
            return
        self._busy = True
        self.apply()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.action_confirm()
        else:
            self.action_cancel()

    @work(exclusive=True)
    async def apply(self) -> None:
        try:
            change = await asyncio.to_thread(
                self.session.apply_bootstrap, self.proposal
            )
        except StaleProposalError:
            await self._fail("Files changed since the preview. Please try again.")
        except (ApplyError, SessionError, OSError) as exc:
            await self._fail(str(exc))
        else:
            if change.state == "reverted":
                reasons = list(change.messages) or ["a reload failed"]
                await self._fail(
                    "A reload failed, so your files were put back as they were.",
                    reasons,
                )
            else:
                self.app.switch_screen(
                    CountdownScreen(
                        self.session, change, left_behind=self.proposal.left_behind
                    )
                )

    async def _fail(self, headline: str, reasons: list[str] | None = None) -> None:
        self.app.pop_screen()
        await self.app.push_screen(ErrorScreen(headline, reasons or []))


class UninstallScreen(Screen[None]):
    """Pick one target or all of them, then preview the uninstall."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.names = sorted(session.adapters)
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Static(
            Text("Uninstall", style=f"bold {GROAT_GOLD}"), classes="diff-header"
        )
        yield Static(
            Text(
                "Remove buckwheat's include lines and files. Your saved values "
                "are kept, so installing again brings them back."
            ),
            classes="op-line",
        )
        yield OptionList("All targets", *self.names, id="uninstall-targets")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def action_back(self) -> None:
        if not self._busy:
            self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._busy:
            return
        # Index 0 is "All targets"; an empty list asks Session for every adapter.
        names = [] if event.option_index == 0 else [self.names[event.option_index - 1]]
        self._busy = True
        self.propose(names)

    @work(exclusive=True)
    async def propose(self, names: list[str]) -> None:
        try:
            proposal = await asyncio.to_thread(self.session.propose_uninstall, names)
        except (SessionError, OSError) as exc:
            self._busy = False
            await self.app.push_screen(
                ErrorScreen(
                    "Could not prepare the uninstall, nothing was changed.", [str(exc)]
                )
            )
            return
        self._busy = False
        await self.app.push_screen(BootstrapScreen(self.session, proposal))


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no question."""

    BINDINGS = [
        Binding("enter,y", "yes", "Yes"),
        Binding("escape,n", "no", "No"),
    ]

    def __init__(self, question: str, details: list[str]) -> None:
        super().__init__()
        self.question = question
        self.details = details

    def compose(self) -> ComposeResult:
        with Vertical(id="error-card"):
            yield Static(Text(self.question, style=f"bold {GROAT_GOLD}"))
            for line in self.details:
                yield Static(Text("- " + line), classes="error-reason")
            yield button_row(
                Button("Yes (Enter)", id="yes", classes="danger"),
                Button("No (Esc)", id="no", classes="primary"),
            )

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class HistoryScreen(Screen[None]):
    """Recorded changes; kept ones can be rolled back."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "rollback", "Roll back"),
    ]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.entries: list[SnapshotInfo] = []

    def compose(self) -> ComposeResult:
        yield Static(Text("History", style=f"bold {GROAT_GOLD}"), classes="diff-header")
        yield DataTable(id="history-table", cursor_type="row")
        yield Static("", id="history-empty")
        yield Footer()

    def on_mount(self) -> None:
        self.reload_table()
        self.query_one(DataTable).focus()

    def reload_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("When", "State", "Files")
        self.entries = self.session.history()
        for info in self.entries:
            table.add_row(
                _when(info.created), info.state, ", ".join(info.paths), key=info.id
            )
        self.query_one("#history-empty", Static).update(
            "" if self.entries else "Nothing here yet."
        )

    def _selected(self) -> SnapshotInfo | None:
        table = self.query_one(DataTable)
        if not self.entries or table.cursor_row >= len(self.entries):
            return None
        return self.entries[table.cursor_row]

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_rollback()

    def action_rollback(self) -> None:
        info = self._selected()
        if info is None:
            return
        if info.state != "kept":
            self.app.notify(f"Only kept changes can be rolled back ({info.state}).")
            return
        self.rollback(info, force=False)

    @work(exclusive=True)
    async def rollback(self, info: SnapshotInfo, force: bool) -> None:
        try:
            result = await asyncio.to_thread(self.session.revert, info.id, force)
        except ModifiedSinceError as exc:
            names = [str(p) for p in exc.paths]
            if await self.app.push_screen_wait(
                ConfirmScreen(
                    "These files changed since the change was applied. "
                    "Rolling back will overwrite your edits. Continue?",
                    names,
                )
            ):
                self.rollback(info, force=True)
            return
        except (SessionError, SnapshotError) as exc:
            self.app.notify(f"Rollback failed: {exc}", severity="error")
            return
        if result.ok:
            self.app.notify("Rolled back.")
        else:
            await self.app.push_screen(
                ErrorScreen("Rolled back, but a reload failed.", list(result.messages))
            )
        self.reload_table()


def _when(created: str) -> str:
    try:
        return datetime.fromisoformat(created).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return created


__all__ = [
    "BootstrapScreen",
    "CountdownScreen",
    "ErrorScreen",
    "HistoryScreen",
    "LeftBehindScreen",
    "PreviewScreen",
    "RequestScreen",
    "UninstallScreen",
    "describe_op",
]
