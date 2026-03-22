"""Sync screen — live progress bars per entity type."""
from datetime import datetime, timezone
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Log, ProgressBar, Static

from clockify_cli.sync.progress import SyncProgress


_ENTITY_LABELS: dict[str, str] = {
    "clients": "Clients",
    "projects": "Projects",
    "users": "Users",
    "time_entries": "Time Entries",
}

_STATUS_MARKUP: dict[str, str] = {
    "pending": "[dim]waiting[/dim]",
    "running": "[yellow]syncing[/yellow]",
    "done": "[green]done[/green]",
    "error": "[red]error[/red]",
}


class SyncScreen(Screen):
    """Displays real-time sync progress for each entity type."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
        Binding("s", "start_sync", "Start Sync"),
        Binding("i", "toggle_incremental", "Toggle Full/Incremental"),
    ]

    progress: reactive[Optional[SyncProgress]] = reactive(None)
    incremental: reactive[bool] = reactive(True)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Static(id="sync-screen"):
            with Static(id="sync-header"):
                yield Label("Sync Data", id="sync-header-title")
                yield Label("", id="sync-mode-label")

            with Static(id="sync-controls"):
                yield Button("Start Sync [s]", id="btn-start", variant="primary")
                yield Button("Back [Esc]", id="btn-back", variant="default")

            with Static(id="sync-entity-list"):
                for entity, label in _ENTITY_LABELS.items():
                    with Static(classes="sync-entity-row", id=f"row-{entity}"):
                        yield Label(label, classes="entity-label")
                        yield ProgressBar(
                            total=100,
                            show_percentage=True,
                            show_eta=False,
                            id=f"pb-{entity}",
                            classes="entity-progress",
                        )
                        yield Label("—", id=f"count-{entity}", classes="entity-count")
                        yield Label(
                            "[dim]waiting[/dim]",
                            id=f"status-{entity}",
                            classes="entity-status",
                        )

            yield Log(id="sync-log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Sync"
        self._update_mode_label()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            self.action_start_sync()
        elif event.button.id == "btn-back":
            self.action_pop_screen()

    def action_start_sync(self) -> None:
        config = self.app.config  # type: ignore[attr-defined]
        if not config.is_configured():
            self._log("[red]Not configured. Go to Settings first.[/red]")
            return
        self.run_worker(self._run_sync(), exclusive=True, name="sync-worker")

    def action_toggle_incremental(self) -> None:
        self.incremental = not self.incremental
        self._update_mode_label()

    def _update_mode_label(self) -> None:
        mode = "Incremental" if self.incremental else "Full"
        self.query_one("#sync-mode-label", Label).update(
            f"Mode: [bold]{mode}[/bold]  (press [i] to toggle)"
        )

    async def _run_sync(self) -> None:
        from clockify_cli.api.client import ClockifyClient
        from clockify_cli.sync.orchestrator import SyncOrchestrator

        config = self.app.config  # type: ignore[attr-defined]
        db = self.app.db  # type: ignore[attr-defined]

        self._log(f"[bold]Starting {'incremental' if self.incremental else 'full'} sync…[/bold]")
        self.query_one("#btn-start", Button).disabled = True

        try:
            async with ClockifyClient(config.get_api_key()) as client:
                orch = SyncOrchestrator(client, db)
                await orch.sync_all(
                    config.workspace_id,
                    incremental=self.incremental,
                    on_progress=self._on_progress,
                )
            # Update last_sync timestamp
            from clockify_cli.config import save_config
            config.last_sync = datetime.now(timezone.utc).isoformat()
            save_config(config)
            self._log("[green]Sync complete![/green]")
        except Exception as exc:
            self._log(f"[red]Sync error: {exc}[/red]")
        finally:
            self.query_one("#btn-start", Button).disabled = False

    async def _on_progress(self, progress: SyncProgress) -> None:
        self.progress = progress

    def watch_progress(self, progress: Optional[SyncProgress]) -> None:
        """Called by Textual when self.progress changes — update all widgets."""
        if progress is None:
            return
        for entity, ep in progress.entities.items():
            try:
                pb = self.query_one(f"#pb-{entity}", ProgressBar)
                count_label = self.query_one(f"#count-{entity}", Label)
                status_label = self.query_one(f"#status-{entity}", Label)

                # Progress bar
                if ep.percent > 0:
                    pb.update(progress=ep.percent)

                # Count
                if ep.records_fetched > 0:
                    count_label.update(
                        f"{ep.records_upserted:,} / {ep.records_fetched:,}"
                    )

                # Status chip
                status_label.update(_STATUS_MARKUP.get(ep.status, ep.status))

                # Log errors
                if ep.status == "error" and ep.error:
                    self._log(f"[red]{entity}: {ep.error}[/red]")
            except Exception:
                pass  # Widget might not exist yet during compose

    def _log(self, message: str) -> None:
        try:
            log = self.query_one("#sync-log", Log)
            ts = datetime.now().strftime("%H:%M:%S")
            log.write_line(f"[dim]{ts}[/dim]  {message}")
        except Exception:
            pass
