"""Time entries browser screen — searchable DataTable over the local DB."""
import asyncio
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, Static


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


def _fmt_datetime(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    return iso.replace("T", " ")[:19]


class TimeEntriesScreen(Screen):
    """Browse locally-stored time entries with search and project filter."""

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
        Binding("/", "focus_search", "Search"),
        Binding("g", "focus_entry_id", "Go to ID"),
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
                yield Input(
                    placeholder="Time Entry ID… [g]",
                    id="entry-id-input",
                )
                yield Button("Open", id="btn-open-entry", variant="primary")
                yield Label("", id="entry-count")
            yield DataTable(id="entries-table", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Time Entries"
        table = self.query_one("#entries-table", DataTable)
        table.add_columns(
            "Date",
            "User",
            "Project",
            "Description",
            "Duration",
            "Billable",
            "Approver Name",
            "Approver ID",
            "Approved At",
        )
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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "entry-id-input":
            self.run_worker(self._open_entry_by_id(), exclusive=False)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "project-filter":
            val = event.value
            self.selected_project = str(val) if val and val != Select.BLANK else None
            self.run_worker(self._load_entries(), exclusive=False)

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_focus_entry_id(self) -> None:
        self.query_one("#entry-id-input", Input).focus()

    def action_refresh(self) -> None:
        self.run_worker(self._load_entries(), exclusive=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-open-entry":
            self.run_worker(self._open_entry_by_id(), exclusive=False)

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
        status_counts = await repo.get_approval_status_counts(config.workspace_id)

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
                row.get("approver_name") or "—",
                row.get("approver_id") or "—",
                _fmt_datetime(row.get("approved_at")),
            )

        count_label = self.query_one("#entry-count", Label)
        count_label.update(
            f"{len(entries):,} entries | "
            f"Pending: {status_counts.get('PENDING', 0):,}  "
            f"Approved: {status_counts.get('APPROVED', 0):,}  "
            f"Not Submitted: {status_counts.get('NOT_SUBMITTED', 0):,}"
        )

    async def _open_entry_by_id(self) -> None:
        """Open a detail screen for one time entry ID."""
        config = self.app.config  # type: ignore[attr-defined]
        db = self.app.db  # type: ignore[attr-defined]
        workspace_id = config.workspace_id
        if not workspace_id:
            return

        entry_id = self.query_one("#entry-id-input", Input).value.strip()
        if not entry_id:
            self.notify("Enter a Time Entry ID first.", severity="warning")
            return

        from clockify_cli.db.repositories.time_entries import TimeEntryRepository
        repo = TimeEntryRepository(db)
        entry = await repo.get_by_id(workspace_id, entry_id)
        if not entry:
            self.notify(f"No local entry found for ID: {entry_id}", severity="warning")
            return
        self.app.push_screen(TimeEntryDetailScreen(entry))


class TimeEntryDetailScreen(Screen):
    """Read-only detail view for a single local time entry."""

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
    ]

    def __init__(self, entry: dict) -> None:
        super().__init__()
        self._entry = entry

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Static(id="entry-detail-screen"):
            yield Label("Time Entry Detail", id="entry-detail-title")
            yield Label(f"ID: {self._entry.get('id', '—')}", classes="entry-detail-line")
            yield Label(
                f"Approval Status: {self._entry.get('approval_status') or 'NOT_SUBMITTED'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Approver Name: {self._entry.get('approver_name') or '—'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Approver ID: {self._entry.get('approver_id') or '—'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Approved At: {_fmt_datetime(self._entry.get('approved_at'))}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Date: {_fmt_date(self._entry.get('start_time'))}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Start: {self._entry.get('start_time') or '—'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"End: {self._entry.get('end_time') or '—'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Duration: {_fmt_duration(self._entry.get('duration'))}",
                classes="entry-detail-line",
            )
            yield Label(
                f"User: {self._entry.get('user_name') or '—'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Project: {self._entry.get('project_name') or '—'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Task ID: {self._entry.get('task_id') or '—'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Billable: {'Yes' if self._entry.get('billable') else 'No'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Description: {self._entry.get('description') or '—'}",
                classes="entry-detail-line",
            )
            yield Label(
                f"Fetched At: {self._entry.get('fetched_at') or '—'}",
                classes="entry-detail-line",
            )
        yield Footer()
