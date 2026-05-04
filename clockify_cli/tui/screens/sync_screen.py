"""Sync screen — live progress bars per entity type."""
from datetime import datetime, timezone
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.dom import NoMatches
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

_STATUS_TEXT: dict[str, str] = {
    "pending": "waiting",
    "running": "syncing",
    "done": "done",
    "error": "error",
}


class SyncScreen(Screen):
    """Displays real-time sync progress for each entity type."""

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
        Binding("s", "start_sync", "Start Sync"),
        Binding("i", "toggle_incremental", "Toggle Full/Incremental"),
        Binding("r", "reset_and_full_sync", "Reset Local + Full Sync"),
    ]

    incremental: reactive[bool] = reactive(True)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Static(id="sync-screen"):
            with Static(id="sync-header"):
                yield Label("Sync Data", id="sync-header-title")
                yield Label("", id="sync-mode-label")

            with Static(id="sync-controls"):
                yield Button("Start Sync [s]", id="btn-start", variant="primary")
                yield Button("Mode: Incremental [i]", id="btn-toggle", variant="default")
                yield Button(
                    "Reset Local + Full Sync [r]",
                    id="btn-reset-full",
                    variant="warning",
                )
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
                            "waiting",
                            id=f"status-{entity}",
                            classes="entity-status status-pending",
                        )

            yield Log(id="sync-log", highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Sync"
        self._update_mode_label()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            self.action_start_sync()
        elif event.button.id == "btn-toggle":
            self.action_toggle_incremental()
        elif event.button.id == "btn-reset-full":
            self.action_reset_and_full_sync()
        elif event.button.id == "btn-back":
            # action_dismiss is async; run_worker executes it (Escape is awaited by Textual).
            self.run_worker(self.action_dismiss(), exclusive=False)

    def action_start_sync(self) -> None:
        self._start_sync(reset_local=False)

    def action_reset_and_full_sync(self) -> None:
        self._start_sync(reset_local=True)

    def _start_sync(self, reset_local: bool) -> None:
        config = self.app.config  # type: ignore[attr-defined]
        if not config.is_configured():
            self._log("Not configured — go to Settings first.")
            return
        if reset_local:
            self._log("Reset-local mode selected: local workspace rows will be deleted first.")
        self.run_worker(
            self._run_sync(reset_local=reset_local),
            exclusive=True,
            name="sync-worker",
        )

    def action_toggle_incremental(self) -> None:
        self.incremental = not self.incremental
        self._update_mode_label()

    async def action_dismiss(self, result=None):
        if getattr(self, "_dismiss_in_progress", False):
            return
        self._dismiss_in_progress = True
        from clockify_cli.tui.worker_utils import cancel_and_wait_running_workers

        await cancel_and_wait_running_workers(self)
        await super().action_dismiss(result)

    def _update_mode_label(self) -> None:
        mode = "Incremental" if self.incremental else "Full"
        self.query_one("#sync-mode-label", Label).update(f"Mode: {mode}")
        self.query_one("#btn-toggle", Button).label = f"Mode: {mode} [i]"

    def _set_sync_action_buttons_disabled(self, disabled: bool) -> None:
        """Toggle Start / Reset-Full buttons; no-op if widgets are gone (e.g. screen dismissed)."""
        for selector in ("#btn-start", "#btn-reset-full"):
            try:
                self.query_one(selector, Button).disabled = disabled
            except NoMatches:
                pass

    async def _run_sync(self, reset_local: bool = False) -> None:
        from clockify_cli.api.client import ClockifyClient
        from clockify_cli.sync.orchestrator import SyncOrchestrator

        config = self.app.config  # type: ignore[attr-defined]
        db = self.app.db  # type: ignore[attr-defined]

        if reset_local:
            mode = "reset-local + full"
        else:
            mode = "incremental" if self.incremental else "full"
        self._log(f"Starting {mode} sync...")
        self._set_sync_action_buttons_disabled(True)

        workspace_id = config.workspace_id
        if not workspace_id or not isinstance(workspace_id, str) or workspace_id.startswith("Select."):
            self._log(
                "No workspace selected. Please go to Settings and save a workspace first."
            )
            self._set_sync_action_buttons_disabled(False)
            return

        try:
            async with ClockifyClient(config.get_api_key()) as client:
                orch = SyncOrchestrator(client, db)
                await orch.sync_all(
                    workspace_id,
                    incremental=False if reset_local else self.incremental,
                    reset_local=reset_local,
                    on_progress=self._on_progress,
                )
            from clockify_cli.config import save_config
            config.last_sync = datetime.now(timezone.utc).isoformat()
            save_config(config)
            self._log("Sync complete!")
        except Exception as exc:
            self._log(f"Sync error: {exc}")
        finally:
            self._set_sync_action_buttons_disabled(False)

    async def _on_progress(self, progress: SyncProgress) -> None:
        """Called by orchestrator after every page — update widgets directly.

        We do NOT use a reactive here because the same SyncProgress object is
        mutated in-place; Textual's reactive system would skip the watcher on
        subsequent calls with the same object reference.
        """
        for entity, ep in progress.entities.items():
            try:
                pb = self.query_one(f"#pb-{entity}", ProgressBar)
                count_label = self.query_one(f"#count-{entity}", Label)
                status_label = self.query_one(f"#status-{entity}", Label)

                # Progress bar (total=100, progress=percent)
                if ep.percent > 0:
                    pb.update(progress=ep.percent)
                elif ep.status == "running":
                    pb.update(progress=1)  # show at least a sliver of activity

                # Record counts
                if ep.records_fetched > 0:
                    count_label.update(
                        f"{ep.records_upserted:,} / {ep.records_fetched:,}"
                    )

                # Status text
                new_status = _STATUS_TEXT.get(ep.status, ep.status)
                status_label.update(new_status)
                # Swap CSS class so colour changes
                for cls in ("status-pending", "status-running", "status-done", "status-error"):
                    status_label.remove_class(cls)
                status_label.add_class(f"status-{ep.status}")

                # Surface errors to the log
                if ep.status == "error" and ep.error:
                    self._log(f"ERROR {entity}: {ep.error}")

            except Exception as exc:
                self._log(f"UI update error ({entity}): {exc}")

    def _log(self, message: str) -> None:
        try:
            log = self.query_one("#sync-log", Log)
            ts = datetime.now().strftime("%H:%M:%S")
            log.write_line(f"{ts}  {message}")
        except Exception:
            pass
