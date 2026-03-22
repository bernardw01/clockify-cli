"""Tests for all repository classes."""
import pytest
from pathlib import Path

from clockify_cli.db.database import Database
from clockify_cli.db.repositories.workspaces import WorkspaceRepository
from clockify_cli.db.repositories.clients import ClientRepository
from clockify_cli.db.repositories.projects import ProjectRepository
from clockify_cli.db.repositories.users import UserRepository
from clockify_cli.db.repositories.time_entries import TimeEntryRepository, _parse_duration
from clockify_cli.db.repositories.sync_log import SyncLogRepository

WS_ID = "ws-001"


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    async with database:
        # Seed a workspace so FK constraints pass
        await database.execute(
            "INSERT OR IGNORE INTO workspaces(id, name) VALUES (?, ?)",
            (WS_ID, "Test Workspace"),
        )
        yield database


# ── WorkspaceRepository ──────────────────────────────────────────────────────

async def test_workspace_upsert_and_get(db: Database):
    repo = WorkspaceRepository(db)
    workspaces = [{"id": WS_ID, "name": "Test Workspace", "currency_code": "USD"}]
    count = await repo.upsert_many(workspaces)
    assert count == 1

    all_ws = await repo.get_all()
    assert len(all_ws) == 1
    assert all_ws[0]["name"] == "Test Workspace"


async def test_workspace_upsert_empty(db: Database):
    repo = WorkspaceRepository(db)
    assert await repo.upsert_many([]) == 0


async def test_workspace_get_by_id(db: Database):
    repo = WorkspaceRepository(db)
    await repo.upsert_many([{"id": WS_ID, "name": "Test Workspace"}])
    ws = await repo.get_by_id(WS_ID)
    assert ws is not None
    assert ws["id"] == WS_ID

    missing = await repo.get_by_id("nonexistent")
    assert missing is None


# ── ClientRepository ─────────────────────────────────────────────────────────

async def test_client_upsert_and_get(db: Database):
    repo = ClientRepository(db)
    clients = [
        {"id": "c-1", "name": "Acme Corp", "archived": False},
        {"id": "c-2", "name": "Old Client", "archived": True},
    ]
    count = await repo.upsert_many(clients, WS_ID)
    assert count == 2

    all_clients = await repo.get_all(WS_ID)
    assert len(all_clients) == 2
    names = {c["name"] for c in all_clients}
    assert names == {"Acme Corp", "Old Client"}


# ── ProjectRepository ─────────────────────────────────────────────────────────

async def test_project_upsert_and_filter_archived(db: Database):
    repo = ProjectRepository(db)
    projects = [
        {"id": "p-1", "name": "Active Project", "archived": False,
         "billable": True, "public": False, "clientId": None},
        {"id": "p-2", "name": "Old Project", "archived": True,
         "billable": False, "public": False, "clientId": None},
    ]
    count = await repo.upsert_many(projects, WS_ID)
    assert count == 2

    active = await repo.get_all(WS_ID, include_archived=False)
    assert len(active) == 1
    assert active[0]["name"] == "Active Project"

    all_projects = await repo.get_all(WS_ID, include_archived=True)
    assert len(all_projects) == 2


# ── UserRepository ────────────────────────────────────────────────────────────

async def test_user_upsert_and_get(db: Database):
    repo = UserRepository(db)
    users = [
        {"id": "u-1", "name": "Alice", "email": "alice@example.com",
         "status": "ACTIVE", "role": "ADMIN", "profilePicture": None},
    ]
    count = await repo.upsert_many(users, WS_ID)
    assert count == 1

    all_users = await repo.get_all(WS_ID)
    assert all_users[0]["email"] == "alice@example.com"

    by_id = await repo.get_by_id("u-1")
    assert by_id is not None
    assert by_id["name"] == "Alice"


# ── TimeEntryRepository ───────────────────────────────────────────────────────

SAMPLE_ENTRY = {
    "id": "te-1",
    "userId": "u-1",
    "projectId": "p-1",
    "description": "Working on tests",
    "billable": True,
    "isLocked": False,
    "taskId": None,
    "tagIds": ["tag-1"],
    "timeInterval": {
        "start": "2024-03-01T09:00:00Z",
        "end": "2024-03-01T10:30:00Z",
        "duration": "PT1H30M",
    },
}


@pytest.fixture
async def db_with_user_project(db: Database):
    """Seed a user and project so FK constraints are satisfied."""
    await db.execute(
        "INSERT OR IGNORE INTO users(id, workspace_id, name) VALUES (?, ?, ?)",
        ("u-1", WS_ID, "Alice"),
    )
    await db.execute(
        "INSERT OR IGNORE INTO projects(id, workspace_id, name) VALUES (?, ?, ?)",
        ("p-1", WS_ID, "Test Project"),
    )
    return db


async def test_time_entry_upsert(db_with_user_project: Database):
    repo = TimeEntryRepository(db_with_user_project)
    count = await repo.upsert_many([SAMPLE_ENTRY], WS_ID)
    assert count == 1

    total = await repo.count(WS_ID)
    assert total == 1


async def test_time_entry_duration_parsed(db_with_user_project: Database):
    repo = TimeEntryRepository(db_with_user_project)
    await repo.upsert_many([SAMPLE_ENTRY], WS_ID)
    row = await db_with_user_project.fetchone(
        "SELECT duration FROM time_entries WHERE id = 'te-1'"
    )
    assert row["duration"] == 5400  # 1h30m = 5400 seconds


async def test_time_entry_latest_time(db_with_user_project: Database):
    repo = TimeEntryRepository(db_with_user_project)
    entries = [
        {**SAMPLE_ENTRY, "id": "te-1",
         "timeInterval": {"start": "2024-01-01T10:00:00Z", "end": "2024-01-01T11:00:00Z"}},
        {**SAMPLE_ENTRY, "id": "te-2",
         "timeInterval": {"start": "2024-03-15T08:00:00Z", "end": "2024-03-15T09:00:00Z"}},
    ]
    await repo.upsert_many(entries, WS_ID)
    latest = await repo.get_latest_entry_time(WS_ID)
    assert latest == "2024-03-15T08:00:00Z"


async def test_time_entry_search_by_description(db_with_user_project: Database):
    repo = TimeEntryRepository(db_with_user_project)
    await repo.upsert_many([SAMPLE_ENTRY], WS_ID)
    results = await repo.search(WS_ID, query="tests")
    assert len(results) == 1

    empty = await repo.search(WS_ID, query="nonexistent")
    assert len(empty) == 0


# ── parse_duration ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("iso,expected", [
    ("PT1H30M15S", 5415),
    ("PT30M", 1800),
    ("PT45S", 45),
    ("PT2H", 7200),
    ("PT0S", 0),
    ("", None),
    ("invalid", None),
])
def test_parse_duration(iso: str, expected):
    assert _parse_duration(iso) == expected


# ── SyncLogRepository ─────────────────────────────────────────────────────────

async def test_sync_log_lifecycle(db: Database):
    repo = SyncLogRepository(db)

    await repo.start_sync(WS_ID, "projects")
    row = await repo.get_last_sync(WS_ID, "projects")
    assert row is not None
    assert row["status"] == "started"

    await repo.complete_sync(WS_ID, "projects", 10, 10, "2024-03-15T08:00:00Z")
    row = await repo.get_last_sync(WS_ID, "projects")
    assert row["status"] == "completed"
    assert row["records_fetched"] == 10
    assert row["last_entry_time"] == "2024-03-15T08:00:00Z"


async def test_sync_log_failure(db: Database):
    repo = SyncLogRepository(db)
    await repo.start_sync(WS_ID, "users")
    await repo.fail_sync(WS_ID, "users", "Connection timeout")
    row = await repo.get_last_sync(WS_ID, "users")
    assert row["status"] == "failed"
    assert "timeout" in row["error_message"]


async def test_sync_log_get_all(db: Database):
    repo = SyncLogRepository(db)
    for entity in ("clients", "projects", "users"):
        await repo.start_sync(WS_ID, entity)
    rows = await repo.get_all_sync_status(WS_ID)
    assert len(rows) == 3


async def test_sync_log_restart_clears_error(db: Database):
    repo = SyncLogRepository(db)
    await repo.start_sync(WS_ID, "clients")
    await repo.fail_sync(WS_ID, "clients", "Network error")
    await repo.start_sync(WS_ID, "clients")  # restart
    row = await repo.get_last_sync(WS_ID, "clients")
    assert row["status"] == "started"
    assert row["error_message"] is None
