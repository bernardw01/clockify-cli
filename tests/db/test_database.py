"""Tests for the Database class and schema setup."""
import pytest
from pathlib import Path

from clockify_cli.db.database import Database
from clockify_cli.db.schema import CURRENT_SCHEMA_VERSION


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    async with database:
        yield database


async def test_connect_creates_file(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    await db.connect()
    assert db_path.exists()
    await db.close()


async def test_schema_version_recorded(db: Database):
    row = await db.fetchone(
        "SELECT version FROM schema_version WHERE version = ?",
        (CURRENT_SCHEMA_VERSION,),
    )
    assert row is not None
    assert row["version"] == CURRENT_SCHEMA_VERSION


async def test_all_tables_created(db: Database):
    expected = {"schema_version", "workspaces", "clients", "projects",
                "users", "time_entries", "sync_log"}
    rows = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    tables = {r["name"] for r in rows}
    assert expected.issubset(tables)


async def test_fetchone_returns_none_for_missing(db: Database):
    result = await db.fetchone(
        "SELECT * FROM workspaces WHERE id = ?", ("nonexistent",)
    )
    assert result is None


async def test_fetchall_returns_list(db: Database):
    result = await db.fetchall("SELECT * FROM workspaces")
    assert isinstance(result, list)
    assert result == []


async def test_double_connect_is_idempotent(tmp_path: Path):
    """Schema application must be safe to run on an existing DB."""
    db_path = tmp_path / "test.db"
    async with Database(db_path):
        pass
    async with Database(db_path) as db:
        row = await db.fetchone(
            "SELECT COUNT(*) AS n FROM schema_version"
        )
        assert row["n"] == 1
