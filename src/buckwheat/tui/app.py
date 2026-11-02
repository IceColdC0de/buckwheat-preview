"""The Textual app. It owns a ``Session`` and nothing else."""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from buckwheat import __version__
from buckwheat.session import Session, SessionError
from buckwheat.tui.screens import RequestScreen
from buckwheat.tui.theme import TCSS_PATH, get_textual_theme


class BuckwheatApp(App[None]):
    TITLE = "buckwheat"
    CSS_PATH = str(TCSS_PATH)
    BINDINGS = [Binding("ctrl+q", "quit", "Quit")]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.sub_title = __version__

    async def on_mount(self) -> None:
        self.register_theme(get_textual_theme())
        self.theme = "buckwheat"
        await self.push_screen(RequestScreen(self.session))
        self.recover()

    def recover(self) -> None:
        """Undo changes left pending by a crash or a closed terminal."""
        try:
            results = self.session.recover()
        except (SessionError, OSError) as exc:
            self.notify(
                f"Could not check for unfinished changes: {exc}", severity="error"
            )
            return
        if results:
            self.notify(
                f"Undid {len(results)} unfinished change(s) from a previous run.",
                severity="warning",
            )
