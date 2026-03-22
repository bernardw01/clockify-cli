"""Async SQLite database connection and helpers."""
import aiosqlite
from pathlib import Path
from loguru import logger

from clockify_cli.db.schema import ALL_DDL, CURRENT_SCHEMA_VERSION


class Database:
    """Manages the aiosqlite connection with WAL mode and schema migrations."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the connection, enable WAL mode, and apply schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Opening database at {self._db_path}")
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._apply_schema()
        logger.debug("Database ready")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.debug("Database closed")

    @property
    def _c(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() must be called first")
        return self._conn

    async def _apply_schema(self) -> None:
        """Run all DDL statements idempotently, then record schema version."""
        for ddl in ALL_DDL:
            await self._c.execute(ddl)
        # Record schema version if not present
        await self._c.execute(
            "INSERT OR IGNORE INTO schema_version(version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        await self._c.commit()
        logger.debug(f"Schema version {CURRENT_SCHEMA_VERSION} applied")

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cursor = await self._c.execute(sql, params)
        await self._c.commit()
        return cursor

    async def executemany(self, sql: str, params: list[tuple]) -> None:
        await self._c.executemany(sql, params)
        await self._c.commit()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self._c.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        async with self._c.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def __aenter__(self) -> "Database":
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
