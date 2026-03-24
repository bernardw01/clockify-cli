"""Unit tests for FiberyPushOrchestrator."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from clockify_cli.db.database import Database
from clockify_cli.fibery.models import PushProgress
from clockify_cli.fibery.push_orchestrator import FiberyPushOrchestrator, _build_payload

WS_ID = "ws-test-123"


# ── test helpers ──────────────────────────────────────────────────────────────

def _make_row(
    id: str = "te-1",
    start_time: str = "2025-12-01T09:00:00Z",
    end_time: str | None = "2025-12-01T10:00:00Z",
    duration: int | None = 3600,
    description: str | None = "Some work",
    task_id: str | None = None,
    project_id: str | None = "proj-1",
    billable: int = 1,
    user_id: str = "user-1",
    user_name: str = "Alice",
    user_email: str = "alice@example.com",
    project_name: str | None = "Alpha",
) -> dict:
    return {
        "id": id,
        "start_time": start_time,
        "end_time": end_time,
        "duration": duration,
        "description": description,
        "task_id": task_id,
        "project_id": project_id,
        "billable": billable,
        "user_id": user_id,
        "user_name": user_name,
        "user_email": user_email,
        "project_name": project_name,
    }


def _make_mock_client(
    existing_ids: set | None = None,
    upsert_count: int = 1,
) -> MagicMock:
    client = MagicMock()
    client.get_existing_time_log_ids = AsyncMock(return_value=existing_ids or set())
    client.batch_upsert_labor_costs = AsyncMock(return_value=upsert_count)
    return client


# ── _build_payload helper ─────────────────────────────────────────────────────

def test_build_payload_maps_fields_correctly():
    row = _make_row()
    payload = _build_payload(row)
    assert payload.time_log_id == "te-1"
    assert payload.seconds == 3600
    assert payload.hours == 1.0
    assert payload.billable == "Yes"
    assert payload.user_id_text == "alice@example.com"


def test_build_payload_billable_false():
    row = _make_row(billable=0)
    payload = _build_payload(row)
    assert payload.billable == "No"


def test_build_payload_maps_user_fields():
    row = _make_row(user_name="Bob", user_email="bob@example.com")
    payload = _build_payload(row)
    assert payload.user_name == "Bob"
    assert payload.user_id_text == "bob@example.com"


def test_build_payload_null_project_gives_none():
    row = _make_row(project_id=None, project_name=None)
    payload = _build_payload(row)
    assert payload.project_id is None
    assert payload.project_name is None


def test_build_payload_null_duration_gives_null_hours():
    row = _make_row(duration=None)
    payload = _build_payload(row)
    assert payload.seconds is None
    assert payload.hours is None


# ── FiberyPushOrchestrator.push_all ──────────────────────────────────────────

async def _seed_db(db: Database, rows: list[dict]) -> None:
    """Insert minimal fixture rows using the real schema (auto-applied on connect)."""
    for r in rows:
        # workspace stub (FK parent)
        await db.execute(
            "INSERT OR IGNORE INTO workspaces(id, name) VALUES (?, ?)",
            (WS_ID, "Test WS"),
        )
        # user stub
        uid = r.get("user_id", "u1")
        await db.execute(
            "INSERT OR IGNORE INTO users(id, workspace_id, name, email) VALUES (?,?,?,?)",
            (uid, WS_ID, r.get("user_name", "Alice"), r.get("user_email", "alice@x.com")),
        )
        # project stub (optional)
        pid = r.get("project_id")
        if pid:
            await db.execute(
                "INSERT OR IGNORE INTO projects(id, workspace_id, name) VALUES (?,?,?)",
                (pid, WS_ID, r.get("project_name", "Alpha")),
            )
        # time entry
        await db.execute(
            """INSERT OR IGNORE INTO time_entries
               (id, workspace_id, user_id, project_id, description,
                start_time, end_time, duration, billable, is_locked)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                r["id"], WS_ID, uid, pid,
                r.get("description"),
                r.get("start_time", "2025-12-01T09:00:00Z"),
                r.get("end_time", "2025-12-01T10:00:00Z"),
                r.get("duration", 3600),
                r.get("billable", 1),
                0,
            ),
        )


@pytest.mark.asyncio
async def test_push_all_pushes_complete_entries(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row()])
        client = _make_mock_client(upsert_count=1)
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "done"
    assert progress.pushed == 1
    assert progress.skipped == 0
    assert progress.errors == 0
    assert progress.created == 1   # te-1 not in existing_ids (empty set)
    assert progress.updated == 0
    client.batch_upsert_labor_costs.assert_called_once()


@pytest.mark.asyncio
async def test_push_all_tracks_updated_entries(tmp_path: Path):
    """Entries whose ID is already in Fibery are counted as updated."""
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row(id="te-existing")])
        client = _make_mock_client(existing_ids={"te-existing"}, upsert_count=1)
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "done"
    assert progress.created == 0
    assert progress.updated == 1


@pytest.mark.asyncio
async def test_push_all_skips_running_timers(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        # running timer: end_time is NULL
        await _seed_db(db, [_make_row(id="te-running", end_time=None, duration=None)])
        client = _make_mock_client()
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "done"
    assert progress.total == 0
    assert progress.skipped == 1
    client.batch_upsert_labor_costs.assert_not_called()


@pytest.mark.asyncio
async def test_push_all_no_entries_returns_done(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        # seed workspace so the DB is initialised but has no entries
        await db.execute(
            "INSERT OR IGNORE INTO workspaces(id, name) VALUES (?, ?)",
            (WS_ID, "Test WS"),
        )
        client = _make_mock_client()
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "done"
    assert progress.pushed == 0


@pytest.mark.asyncio
async def test_push_all_preflight_failure_returns_error(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        client = MagicMock()
        client.get_existing_time_log_ids = AsyncMock(side_effect=Exception("network error"))
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "error"
    assert "Pre-flight failed" in (progress.error_message or "")


@pytest.mark.asyncio
async def test_push_all_batch_error_recorded_in_progress(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row()])
        client = _make_mock_client()
        client.batch_upsert_labor_costs = AsyncMock(side_effect=Exception("API down"))
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.errors == 1
    assert progress.status == "error"


@pytest.mark.asyncio
async def test_push_all_invokes_progress_callback(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    callbacks: list[PushProgress] = []

    async def on_progress(p: PushProgress) -> None:
        callbacks.append(p)

    async with db:
        await _seed_db(db, [_make_row()])
        client = _make_mock_client(upsert_count=1)
        orch = FiberyPushOrchestrator(client, db)
        await orch.push_all(WS_ID, on_progress=on_progress)

    assert len(callbacks) >= 2  # at least start + completion notifications
