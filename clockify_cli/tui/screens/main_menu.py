"""Main menu screen — navigation hub and sync status overview."""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from clockify_cli.constants import APP_NAME, APP_VERSION


class MainMenuScreen(Screen):
    """Navigation hub shown on launch."""

    BINDINGS = [
        Binding("s", "sync", "Sync"),
        Binding("e", "entries", "Entries"),
        Binding("f", "fibery", "Push to Fibery"),
        Binding("comma", "settings", "Settings"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="main-menu-screen")
        with Static(id="menu-panel"):
            yield Label(f"[bold]{APP_NAME}[/bold] v{APP_VERSION}", id="menu-title")
            yield Label("", id="workspace-label")
            yield Label("", id="sync-status")
            yield Button("  Sync Data", id="btn-sync", classes="menu-item")
            yield Button("  Browse Entries", id="btn-entries", classes="menu-item")
            yield Button("  Push to Fibery", id="btn-fibery", classes="menu-item")
            yield Button("  Settings", id="btn-settings", classes="menu-item")
            yield Button("  Quit", id="btn-quit", classes="menu-item")
        yield Footer()

    def on_mount(self) -> None:
        self.title = APP_NAME
        self._refresh_labels()

    def on_screen_resume(self) -> None:
        """Re-read config/db each time we return to the menu."""
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        config = self.app.config  # type: ignore[attr-defined]
        ws_label = self.query_one("#workspace-label", Label)
        sync_label = self.query_one("#sync-status", Label)

        if config.workspace_name:
            ws_label.update(f"Workspace: [bold]{config.workspace_name}[/bold]")
        else:
            ws_label.update("[dim]No workspace configured[/dim]")

        if config.last_sync:
            sync_label.update(f"Last sync: {config.last_sync[:19].replace('T', ' ')} UTC")
        else:
            sync_label.update("[dim]Never synced[/dim]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-sync":
            self.action_sync()
        elif btn_id == "btn-entries":
            self.action_entries()
        elif btn_id == "btn-fibery":
            self.action_fibery()
        elif btn_id == "btn-settings":
            self.action_settings()
        elif btn_id == "btn-quit":
            self.app.exit()

    def action_sync(self) -> None:
        from clockify_cli.tui.screens.sync_screen import SyncScreen
        self.app.push_screen(SyncScreen())

    def action_entries(self) -> None:
        from clockify_cli.tui.screens.time_entries import TimeEntriesScreen
        self.app.push_screen(TimeEntriesScreen())

    def action_fibery(self) -> None:
        from clockify_cli.tui.screens.fibery_push_screen import FiberyPushScreen
        self.app.push_screen(FiberyPushScreen())

    def action_settings(self) -> None:
        from clockify_cli.tui.screens.settings import SettingsScreen
        self.app.push_screen(SettingsScreen())
