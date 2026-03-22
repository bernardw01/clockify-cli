"""Root Textual application. Full implementation in Phase 5."""
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Label

from clockify_cli.config import Config
from clockify_cli.constants import APP_NAME, APP_VERSION


class ClockifyApp(App[None]):
    """Main Clockify TUI application."""

    CSS = """
    Screen {
        align: center middle;
    }
    Label {
        padding: 1 2;
    }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label(f"[bold]{APP_NAME} v{APP_VERSION}[/bold]\n\nFull TUI coming in Phase 5.")
        yield Footer()

    def on_mount(self) -> None:
        self.title = APP_NAME
        self.sub_title = f"v{APP_VERSION}"
