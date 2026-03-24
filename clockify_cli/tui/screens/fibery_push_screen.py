"""Fibery push screen — live progress for pushing time entries to Fibery."""
from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Log, ProgressBar, Static

from clockify_cli.fibery.models import PushProgress


class FiberyPushScreen(Screen):
    """Pushes time entries from local SQLite into Fibery Labor Costs."""

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
        Binding("s", "start_push", "Start Push"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Static(id="sync-screen"):          # reuse sync-screen CSS
            with Static(id="sync-header"):
                yield Label("Push to Fibery", id="sync-header-title")
                yield Label(
                    "Pushes all completed time entries → Fibery Labor Costs",
                    id="sync-mode-label",
                )

            with Static(id="sync-controls"):
                yield Button("Start Push [s]", id="btn-start", variant="primary")
                yield Button("Back [Esc]", id="btn-back", variant="default")

            with Static(id="sync-entity-list"):
                with Static(classes="sync-entity-row", id="row-labor-costs"):
                    yield Label("Labor Costs", classes="entity-label")
                    yield ProgressBar(
                        total=100,
                        show_percentage=True,
                        show_eta=False,
                        id="pb-labor-costs",
                        classes="entity-progress",
                    )
                    yield Label("—", id="count-labor-costs", classes="entity-count")
                    yield Label(
                        "waiting",
                        id="status-labor-costs",
                        classes="entity-status status-pending",
                    )

            yield Log(id="sync-log", highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Push to Fibery"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            self.action_start_push()
        elif event.button.id == "btn-back":
            self.action_dismiss()

    def action_start_push(self) -> None:
        config = self.app.config  # type: ignore[attr-defined]
        if not config.is_configured():
            self._log("Not configured — set Clockify API key in Settings first.")
            return
        if not config.is_fibery_configured():
            self._log("No Fibery API key — add it in Settings first.")
            return
        self.run_worker(self._run_push(), exclusive=True, name="fibery-push-worker")

    async def _run_push(self) -> None:
        from clockify_cli.fibery.client import FiberyClient
        from clockify_cli.fibery.push_orchestrator import FiberyPushOrchestrator

        config = self.app.config  # type: ignore[attr-defined]
        db = self.app.db          # type: ignore[attr-defined]

        self._log("Starting full reconciliation push to Fibery...")
        self.query_one("#btn-start", Button).disabled = True

        # Resolve a valid workspace ID — config may hold a sentinel string
        # ("Select.NULL") if Settings was saved without re-fetching workspaces.
        workspace_id = config.workspace_id
        if not workspace_id or not isinstance(workspace_id, str) or workspace_id.startswith("Select."):
            rows = await db.fetchall("SELECT id FROM workspaces LIMIT 1", ())
            if not rows:
                self._log(
                    "No workspace found. Please sync first, then re-save Settings."
                )
                self.query_one("#btn-start", Button).disabled = False
                return
            workspace_id = rows[0]["id"]
            self._log(f"Note: resolved workspace from DB ({workspace_id[:8]}…)")

        try:
            async with FiberyClient(
                config.get_fibery_api_key(), config.fibery_workspace
            ) as client:
                orch = FiberyPushOrchestrator(client, db)
                result = await orch.push_all(
                    workspace_id,
                    on_progress=self._on_progress,
                )

            if result.status == "done":
                self._log(
                    f"Push complete! {result.created:,} new, "
                    f"{result.updated:,} updated, "
                    f"{result.skipped:,} skipped (running timers)."
                )
            else:
                self._log(
                    f"Push finished with errors: {result.error_message}  "
                    f"({result.created:,} new, {result.updated:,} updated, "
                    f"{result.errors:,} failed)"
                )
        except Exception as exc:
            self._log(f"Push error: {exc}")
        finally:
            self.query_one("#btn-start", Button).disabled = False

    async def _on_progress(self, progress: PushProgress) -> None:
        """Update widgets directly — same pattern as SyncScreen."""
        try:
            pb = self.query_one("#pb-labor-costs", ProgressBar)
            count_label = self.query_one("#count-labor-costs", Label)
            status_label = self.query_one("#status-labor-costs", Label)

            if progress.percent > 0:
                pb.update(progress=progress.percent)
            elif progress.status == "running":
                pb.update(progress=1)

            if progress.total > 0:
                count_label.update(f"{progress.pushed:,} / {progress.total:,}")

            status_text = {
                "pending": "waiting",
                "running": "pushing",
                "done": "done",
                "error": "error",
            }.get(progress.status, progress.status)
            status_label.update(status_text)

            for cls in ("status-pending", "status-running", "status-done", "status-error"):
                status_label.remove_class(cls)
            status_label.add_class(f"status-{progress.status}")

            if progress.status == "running" and progress.pushed > 0:
                self._log(f"Pushed {progress.pushed:,} / {progress.total:,} entries...")

            if progress.status in ("done", "error") and progress.errors:
                self._log(f"ERROR: {progress.error_message}")

        except Exception as exc:
            self._log(f"UI update error: {exc}")

    def _log(self, message: str) -> None:
        try:
            log = self.query_one("#sync-log", Log)
            ts = datetime.now().strftime("%H:%M:%S")
            log.write_line(f"{ts}  {message}")
        except Exception:
            pass
