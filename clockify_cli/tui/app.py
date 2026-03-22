"""Root Textual application."""
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding

from clockify_cli.config import Config
from clockify_cli.constants import APP_NAME, APP_VERSION, DB_PATH
from clockify_cli.db.database import Database


class ClockifyApp(App[None]):
    """Main Clockify TUI application."""

    CSS_PATH = Path(__file__).parent / "styles.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: Config, db_path: Path = DB_PATH) -> None:
        super().__init__()
        self.config = config
        self.db = Database(db_path)

    def compose(self) -> ComposeResult:
        # No widgets here — screens handle all composition
        return iter([])

    async def on_mount(self) -> None:
        self.title = APP_NAME
        self.sub_title = f"v{APP_VERSION}"

        # Open DB connection
        await self.db.connect()

        # Route to settings if unconfigured, otherwise main menu
        if not self.config.is_configured():
            from clockify_cli.tui.screens.settings import SettingsScreen
            await self.push_screen(SettingsScreen())
        else:
            from clockify_cli.tui.screens.main_menu import MainMenuScreen
            await self.push_screen(MainMenuScreen())

    async def on_unmount(self) -> None:
        await self.db.close()
