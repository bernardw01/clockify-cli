"""Tests for the SyncOrchestrator using mocked API client and in-memory DB."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from clockify_cli.api.client import ClockifyClient
from clockify_cli.api.exceptions import AuthError
from clockify_cli.api.models import Client, Project, TimeEntry, WorkspaceUser
from clockify_cli.db.database import Database
from clockify_cli.sync.orchestrator import SyncOrchestrator
from clockify_cli.sync.progress import SyncProgress

WS_ID = "ws-001"


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    async with database:
        await database.execute(
            "INSERT OR IGNORE INTO workspaces(id, name) VALUES (?, ?)",
            (WS_ID, "Test Workspace"),
        )
        yield database


def make_mock_client(
    clients: list | None = None,
    projects: list | None = None,
    users: list | None = None,
    time_entry_pages: list | None = None,
) -> ClockifyClient:
    """Build a ClockifyClient with async-mocked methods."""
    mock = MagicMock(spec=ClockifyClient)

    mock.get_clients = AsyncMock(return_value=clients or [])
    mock.get_projects = AsyncMock(return_value=projects or [])
    mock.get_users = AsyncMock(return_value=users or [])

    async def _iter_time_entries(*args, **kwargs):
        for i, page in enumerate(time_entry_pages or []):
            yield page, i + 1, len(time_entry_pages or [])

    mock.iter_time_entries = _iter_time_entries
    return mock


# ── helper builders ────────────────────────────────────────────────────────────

def make_client(id: str = "c-1") -> Client:
    return Client.model_validate({"id": id, "name": f"Client {id}", "workspaceId": WS_ID})


def make_project(id: str = "p-1") -> Project:
    return Project.model_validate({
        "id": id, "name": f"Project {id}", "workspaceId": WS_ID,
        "clientId": None, "archived": False, "billable": False, "public": False,
    })


def make_user(id: str = "u-1") -> WorkspaceUser:
    return WorkspaceUser.model_validate({"id": id, "name": f"User {id}", "status": "ACTIVE"})


def make_entry(id: str = "te-1", user_id: str = "u-1") -> TimeEntry:
    return TimeEntry.model_validate({
        "id": id, "workspaceId": WS_ID, "userId": user_id,
        "projectId": None, "taskId": None, "description": "Test entry",
        "billable": False, "isLocked": False, "tagIds": [],
        "timeInterval": {"start": "2024-03-01T09:00:00Z", "end": "2024-03-01T10:00:00Z"},
    })


# ── tests ──────────────────────────────────────────────────────────────────────

async def test_sync_all_empty_workspace(db: Database):
    """Sync with no data should complete without errors."""
    client = make_mock_client()
    orch = SyncOrchestrator(client, db)
    progress = await orch.sync_all(WS_ID, incremental=False)

    assert progress.is_done
    assert not progress.has_errors
    assert progress.completed_at is not None


async def test_sync_clients_persisted(db: Database):
    client = make_mock_client(clients=[make_client("c-1"), make_client("c-2")])
    orch = SyncOrchestrator(client, db)
    progress = await orch.sync_all(WS_ID, incremental=False)

    assert progress.entities["clients"].records_upserted == 2
    rows = await db.fetchall("SELECT * FROM clients WHERE workspace_id = ?", (WS_ID,))
    assert len(rows) == 2


async def test_sync_projects_persisted(db: Database):
    client = make_mock_client(projects=[make_project("p-1")])
    orch = SyncOrchestrator(client, db)
    progress = await orch.sync_all(WS_ID, incremental=False)

    assert progress.entities["projects"].records_upserted == 1


async def test_sync_users_persisted(db: Database):
    client = make_mock_client(users=[make_user("u-1"), make_user("u-2")])
    orch = SyncOrchestrator(client, db)
    progress = await orch.sync_all(WS_ID, incremental=False)

    assert progress.entities["users"].records_upserted == 2


async def test_sync_time_entries_persisted(db: Database):
    user = make_user("u-1")
    entry = make_entry("te-1", "u-1")

    # Seed user so FK constraint is satisfied
    await db.execute(
        "INSERT OR IGNORE INTO users(id, workspace_id, name) VALUES (?, ?, ?)",
        ("u-1", WS_ID, "Alice"),
    )

    client = make_mock_client(
        users=[user],
        time_entry_pages=[[entry]],
    )
    orch = SyncOrchestrator(client, db)
    progress = await orch.sync_all(WS_ID, incremental=False)

    assert progress.entities["time_entries"].records_upserted == 1
    count = await db.fetchone(
        "SELECT COUNT(*) AS n FROM time_entries WHERE workspace_id = ?", (WS_ID,)
    )
    assert count["n"] == 1


async def test_progress_callback_invoked(db: Database):
    callback_calls: list[SyncProgress] = []

    async def on_progress(p: SyncProgress) -> None:
        callback_calls.append(p)

    client = make_mock_client(
        clients=[make_client()],
        users=[make_user()],
    )
    # Seed user for FK
    await db.execute(
        "INSERT OR IGNORE INTO users(id, workspace_id, name) VALUES (?, ?, ?)",
        ("u-1", WS_ID, "Alice"),
    )
    orch = SyncOrchestrator(client, db)
    await orch.sync_all(WS_ID, incremental=False, on_progress=on_progress)

    assert len(callback_calls) > 0


async def test_sync_log_completed_after_sync(db: Database):
    client = make_mock_client(clients=[make_client()])
    orch = SyncOrchestrator(client, db)
    await orch.sync_all(WS_ID, incremental=False)

    row = await db.fetchone(
        "SELECT * FROM sync_log WHERE workspace_id = ? AND entity_type = 'clients'",
        (WS_ID,),
    )
    assert row is not None
    assert row["status"] == "completed"
    assert row["records_fetched"] == 1


async def test_client_api_error_marks_entity_error(db: Database):
    mock = make_mock_client()
    mock.get_clients = AsyncMock(side_effect=AuthError("Bad key"))

    orch = SyncOrchestrator(mock, db)
    progress = await orch.sync_all(WS_ID, incremental=False)

    assert progress.entities["clients"].status == "error"
    assert "Bad key" in (progress.entities["clients"].error or "")
    # Other entities should still run
    assert progress.entities["projects"].status == "done"


async def test_sync_progress_dataclass():
    p = SyncProgress(workspace_id="ws-1", incremental=True)
    assert not p.is_done  # all entities start as "pending"
    assert p.total_records == 0
    for ep in p.entities.values():
        ep.status = "done"
    assert p.is_done
