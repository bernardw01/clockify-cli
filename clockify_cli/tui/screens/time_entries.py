"""Time entries browser screen — searchable DataTable over the local DB."""
import asyncio
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Label, Select, Static


def _fmt_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "—"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    return iso[:10]


class TimeEntriesScreen(Screen):
    """Browse locally-stored time entries with search and project filter."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
        Binding("/", "focus_search", "Search"),
        Binding("r", "refresh", "Refresh"),
    ]

    search_query: reactive[str] = reactive("")
    selected_project: reactive[Optional[str]] = reactive(None)
    _debounce_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Static(id="entries-screen"):
            with Static(id="entries-toolbar"):
                yield Input(
                    placeholder="Search description… [/]",
                    id="search-input",
                    classes="",
                )
                yield Select(
                    options=[],
                    prompt="All Projects",
                    id="project-filter",
                    allow_blank=True,
                )
                yield Label("", id="entry-count")
            yield DataTable(id="entries-table", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Time Entries"
        table = self.query_one("#entries-table", DataTable)
        table.add_columns("Date", "User", "Project", "Description", "Duration", "Billable")
        self.run_worker(self._load_projects(), exclusive=False)
        self.run_worker(self._load_entries(), exclusive=False)

    def on_screen_resume(self) -> None:
        self.run_worker(self._load_entries(), exclusive=False)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            # Debounce: wait 300 ms after last keystroke before querying
            if self._debounce_task:
                self._debounce_task.cancel()
            self._debounce_task = self.set_timer(
                0.3, lambda: self.run_worker(self._load_entries(), exclusive=False)
            )
            self.search_query = event.value

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "project-filter":
            val = event.value
            self.selected_project = str(val) if val and val != Select.BLANK else None
            self.run_worker(self._load_entries(), exclusive=False)

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_refresh(self) -> None:
        self.run_worker(self._load_entries(), exclusive=False)

    async def _load_projects(self) -> None:
        """Populate the project filter dropdown from the local DB."""
        config = self.app.config  # type: ignore[attr-defined]
        db = self.app.db  # type: ignore[attr-defined]
        if not config.workspace_id:
            return
        from clockify_cli.db.repositories.projects import ProjectRepository
        repo = ProjectRepository(db)
        projects = await repo.get_all(config.workspace_id, include_archived=True)
        options = [(p["name"], p["id"]) for p in projects]
        select = self.query_one("#project-filter", Select)
        select.set_options(options)

    async def _load_entries(self) -> None:
        """Query DB and repopulate the DataTable."""
        config = self.app.config  # type: ignore[attr-defined]
        db = self.app.db  # type: ignore[attr-defined]
        if not config.workspace_id:
            return

        from clockify_cli.db.repositories.time_entries import TimeEntryRepository
        repo = TimeEntryRepository(db)
        entries = await repo.search(
            workspace_id=config.workspace_id,
            query=self.search_query,
            project_id=self.selected_project,
            limit=500,
        )

        table = self.query_one("#entries-table", DataTable)
        table.clear()
        for row in entries:
            table.add_row(
                _fmt_date(row.get("start_time")),
                row.get("user_name") or "—",
                row.get("project_name") or "—",
                (row.get("description") or "")[:60],
                _fmt_duration(row.get("duration")),
                "✓" if row.get("billable") else "",
            )

        count_label = self.query_one("#entry-count", Label)
        count_label.update(f"{len(entries):,} entries")
