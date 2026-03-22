"""Settings screen — API key input and workspace selector."""
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static


class SettingsScreen(Screen):
    """Configure API key and active workspace."""

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Maps workspace_id → workspace_name; populated after fetch
        self._workspace_names: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Static(id="settings-screen"):
            with Static(id="settings-panel"):
                yield Label("Settings", id="settings-title")

                yield Label("Clockify API Key", classes="settings-label")
                yield Input(
                    placeholder="Paste your API key here",
                    password=True,
                    id="api-key-input",
                    classes="settings-input",
                )

                yield Label("Workspace", classes="settings-label")
                yield Select(
                    options=[],
                    prompt="— fetch workspaces after entering key —",
                    id="workspace-select",
                    classes="settings-select",
                    allow_blank=True,
                )

                yield Button("Fetch Workspaces", id="btn-fetch", variant="default")
                yield Button("Save", id="btn-save", variant="primary")
                yield Label("", id="settings-status")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Settings"
        config = self.app.config  # type: ignore[attr-defined]
        if config.api_key:
            self.query_one("#api-key-input", Input).value = config.api_key

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-fetch":
            self.run_worker(self._fetch_workspaces(), exclusive=True)
        elif event.button.id == "btn-save":
            self.action_save()

    async def _fetch_workspaces(self) -> None:
        from clockify_cli.api.client import ClockifyClient
        from clockify_cli.api.exceptions import ClockifyAPIError

        status = self.query_one("#settings-status", Label)
        api_key = self.query_one("#api-key-input", Input).value.strip()
        if not api_key:
            status.update("[red]Enter an API key first.[/red]")
            return

        status.update("[yellow]Fetching workspaces...[/yellow]")
        try:
            async with ClockifyClient(api_key) as client:
                workspaces = await client.get_workspaces()

            self._workspace_names = {ws.id: ws.name for ws in workspaces}
            select = self.query_one("#workspace-select", Select)
            options = [(ws.name, ws.id) for ws in workspaces]
            select.set_options(options)

            # Pre-select the currently configured workspace
            config = self.app.config  # type: ignore[attr-defined]
            if config.workspace_id and config.workspace_id in self._workspace_names:
                select.value = config.workspace_id  # type: ignore[assignment]

            status.update(f"[green]Found {len(workspaces)} workspace(s).[/green]")
        except ClockifyAPIError as exc:
            status.update(f"[red]Error: {exc}[/red]")

    def action_save(self) -> None:
        from clockify_cli.config import save_config

        api_key = self.query_one("#api-key-input", Input).value.strip()
        select = self.query_one("#workspace-select", Select)
        status = self.query_one("#settings-status", Label)

        if not api_key:
            status.update("[red]API key is required.[/red]")
            return

        config = self.app.config  # type: ignore[attr-defined]
        config.api_key = api_key

        ws_value: Optional[str] = None
        try:
            raw = select.value
            ws_value = str(raw) if raw and raw != Select.BLANK else None
        except Exception:
            ws_value = None

        if ws_value:
            config.workspace_id = ws_value
            config.workspace_name = self._workspace_names.get(ws_value, ws_value)

        save_config(config)
        status.update("[green]Settings saved.[/green]")
