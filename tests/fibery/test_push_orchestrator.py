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
    approval_status: str = "PENDING",
    user_id: str = "user-1",
    user_name: str = "Alice",
    user_email: str = "alice@example.com",
    project_name: str | None = "Alpha",
    fetched_at: str = "2025-12-01T11:00:00Z",
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
        "approval_status": approval_status,
        "user_id": user_id,
        "user_name": user_name,
        "user_email": user_email,
        "project_name": project_name,
        "fetched_at": fetched_at,
    }


def _make_mock_client(
    existing_ids: set | None = None,
    upsert_count: int = 1,
) -> MagicMock:
    client = MagicMock()
    client.get_last_clockify_update_run_at = AsyncMock(
        return_value="2025-12-01T11:00:00Z"
    )
    client.append_clockify_update_log = AsyncMock(return_value=None)
    client.get_labor_cost_entity_ids = AsyncMock(return_value=[])
    client.delete_labor_cost_entities = AsyncMock(return_value=0)
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


def test_build_payload_maps_approval_status():
    row = _make_row(approval_status="APPROVED")
    payload = _build_payload(row)
    assert payload.approval_status == "APPROVED"


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


# ── helpers for DB seeding ────────────────────────────────────────────────────

async def _seed_db(db: Database, rows: list[dict]) -> None:
    """Insert minimal fixture rows using the real schema (auto-applied on connect)."""
    for r in rows:
        await db.execute(
            "INSERT OR IGNORE INTO workspaces(id, name) VALUES (?, ?)",
            (WS_ID, "Test WS"),
        )
        uid = r.get("user_id", "u1")
        await db.execute(
            "INSERT OR IGNORE INTO users(id, workspace_id, name, email) VALUES (?,?,?,?)",
            (uid, WS_ID, r.get("user_name", "Alice"), r.get("user_email", "alice@x.com")),
        )
        pid = r.get("project_id")
        if pid:
            await db.execute(
                "INSERT OR IGNORE INTO projects(id, workspace_id, name) VALUES (?,?,?)",
                (pid, WS_ID, r.get("project_name", "Alpha")),
            )
        await db.execute(
            """INSERT OR IGNORE INTO time_entries
               (id, workspace_id, user_id, project_id, description,
                start_time, end_time, duration, billable, is_locked, approval_status, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r["id"], WS_ID, uid, pid,
                r.get("description"),
                r.get("start_time", "2025-12-01T09:00:00Z"),
                r.get("end_time", "2025-12-01T10:00:00Z"),
                r.get("duration", 3600),
                r.get("billable", 1),
                0,
                r.get("approval_status", "PENDING"),
                r.get("fetched_at", "2025-12-01T11:00:00Z"),
            ),
        )


# ── FiberyPushOrchestrator.push_all — full push ───────────────────────────────

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
    assert progress.created == 1
    assert progress.updated == 0
    assert progress.is_incremental is True
    client.batch_upsert_labor_costs.assert_called_once()
    client.append_clockify_update_log.assert_called_once()


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
        await db.execute(
            "INSERT OR IGNORE INTO workspaces(id, name) VALUES (?, ?)",
            (WS_ID, "Test WS"),
        )
        client = _make_mock_client()
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "done"
    assert progress.pushed == 0


# ── incremental push behaviour ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_push_all_filters_out_not_submitted_entries(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row(id="te-ns", approval_status="NOT_SUBMITTED")])
        client = _make_mock_client()
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "done"
    assert progress.total == 0
    client.batch_upsert_labor_costs.assert_not_called()

@pytest.mark.asyncio
async def test_push_all_incremental_filters_old_entries(tmp_path: Path):
    """Entries fetched before checkpoint should be excluded."""
    db = Database(tmp_path / "test.db")
    async with db:
        # Entry fetched at 10:00, checkpoint was 11:00 → should be skipped
        await _seed_db(db, [_make_row(id="te-old", fetched_at="2025-12-01T10:00:00Z")])
        client = _make_mock_client()
        client.get_last_clockify_update_run_at = AsyncMock(
            return_value="2025-12-01T11:00:00Z"
        )
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "done"
    assert progress.total == 0          # old entry filtered out
    assert progress.is_incremental is True
    client.batch_upsert_labor_costs.assert_not_called()


@pytest.mark.asyncio
async def test_push_all_incremental_includes_new_entries(tmp_path: Path):
    """Entries fetched at/after checkpoint should be included."""
    db = Database(tmp_path / "test.db")
    async with db:
        # Entry fetched at 12:00, checkpoint was 11:00 → should be included
        await _seed_db(db, [_make_row(id="te-new", fetched_at="2025-12-01T12:00:00Z")])
        client = _make_mock_client(upsert_count=1)
        client.get_last_clockify_update_run_at = AsyncMock(
            return_value="2025-12-01T11:00:00Z"
        )
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "done"
    assert progress.total == 1
    assert progress.is_incremental is True
    client.batch_upsert_labor_costs.assert_called_once()


@pytest.mark.asyncio
async def test_push_all_incremental_includes_entries_equal_to_checkpoint(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row(id="te-eq", fetched_at="2025-12-01T11:00:00Z")])
        client = _make_mock_client(upsert_count=1)
        client.get_last_clockify_update_run_at = AsyncMock(
            return_value="2025-12-01T11:00:00Z"
        )
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "done"
    assert progress.total == 1
    client.batch_upsert_labor_costs.assert_called_once()


@pytest.mark.asyncio
async def test_push_all_replace_all_ignores_incremental_cursor(tmp_path: Path):
    """Replace-all mode should delete Fibery rows and then perform a full push."""
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row(id="te-old", fetched_at="2025-12-01T10:00:00Z")])
        client = _make_mock_client(upsert_count=1)
        client.get_labor_cost_entity_ids = AsyncMock(
            return_value=["11111111-1111-1111-1111-111111111111"]
        )
        client.delete_labor_cost_entities = AsyncMock(return_value=1)
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID, replace_all=True)

    assert progress.status == "done"
    assert progress.is_incremental is False
    assert progress.total == 1
    client.delete_labor_cost_entities.assert_called_once()
    client.batch_upsert_labor_costs.assert_called_once()


@pytest.mark.asyncio
async def test_push_all_appends_update_log_on_success(tmp_path: Path):
    """Fibery Clockify Update Log must be written after a clean push."""
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row()])
        client = _make_mock_client(upsert_count=1)
        orch = FiberyPushOrchestrator(client, db)
        await orch.push_all(WS_ID)
    client.append_clockify_update_log.assert_called_once()


@pytest.mark.asyncio
async def test_push_all_appends_update_log_on_error(tmp_path: Path):
    """Fibery Clockify Update Log must still be written on failed push."""
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row()])
        client = _make_mock_client()
        client.batch_upsert_labor_costs = AsyncMock(side_effect=Exception("API down"))
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)
    assert progress.status == "error"
    client.append_clockify_update_log.assert_called_once()


# ── error paths ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_push_all_preflight_failure_returns_error(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        client = MagicMock()
        client.get_last_clockify_update_run_at = AsyncMock(
            return_value="2025-12-01T11:00:00Z"
        )
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
async def test_push_all_last_update_query_failure_returns_error(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        client = _make_mock_client()
        client.get_last_clockify_update_run_at = AsyncMock(side_effect=Exception("api down"))
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "error"
    assert "Clockify Update Log query failed" in (progress.error_message or "")


@pytest.mark.asyncio
async def test_push_all_empty_update_log_blocks_incremental_with_recommendation(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row()])
        client = _make_mock_client()
        client.get_last_clockify_update_run_at = AsyncMock(return_value=None)
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID)

    assert progress.status == "error"
    assert "Clockify Update Log is empty" in (progress.error_message or "")
    assert "full data refresh" in (progress.error_message or "")
    client.batch_upsert_labor_costs.assert_not_called()


@pytest.mark.asyncio
async def test_push_all_replace_all_delete_failure_returns_error(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    async with db:
        await _seed_db(db, [_make_row()])
        client = _make_mock_client()
        client.get_labor_cost_entity_ids = AsyncMock(return_value=["id-1"])
        client.delete_labor_cost_entities = AsyncMock(side_effect=Exception("delete failed"))
        orch = FiberyPushOrchestrator(client, db)
        progress = await orch.push_all(WS_ID, replace_all=True)

    assert progress.status == "error"
    assert "Replace-all delete failed" in (progress.error_message or "")


@pytest.mark.asyncio
async def test_push_all_replace_all_reports_delete_phase_progress(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    snapshots: list[tuple[str, int, int]] = []

    async def on_progress(p: PushProgress) -> None:
        snapshots.append((p.phase, p.pushed, p.total))

    async with db:
        await _seed_db(db, [_make_row()])
        client = _make_mock_client(upsert_count=1)
        client.get_labor_cost_entity_ids = AsyncMock(
            return_value=["id-1", "id-2", "id-3"]
        )
        client.delete_labor_cost_entities = AsyncMock(return_value=3)
        orch = FiberyPushOrchestrator(client, db)
        await orch.push_all(WS_ID, replace_all=True, on_progress=on_progress)

    assert ("deleting", 0, 3) in snapshots


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
