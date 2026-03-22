"""Repository for the sync_log table — tracks last sync state per entity."""
from datetime import datetime, timezone
from typing import Optional

from clockify_cli.db.database import Database


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncLogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def start_sync(self, workspace_id: str, entity_type: str) -> None:
        await self._db.execute(
            """
            INSERT INTO sync_log (workspace_id, entity_type, status, started_at)
            VALUES (?, ?, 'started', ?)
            ON CONFLICT(workspace_id, entity_type) DO UPDATE SET
                status = 'started',
                started_at = excluded.started_at,
                completed_at = NULL,
                records_fetched = 0,
                records_upserted = 0,
                error_message = NULL
            """,
            (workspace_id, entity_type, _now_iso()),
        )

    async def complete_sync(
        self,
        workspace_id: str,
        entity_type: str,
        records_fetched: int,
        records_upserted: int,
        last_entry_time: Optional[str] = None,
    ) -> None:
        await self._db.execute(
            """
            UPDATE sync_log SET
                status = 'completed',
                completed_at = ?,
                records_fetched = ?,
                records_upserted = ?,
                last_entry_time = COALESCE(?, last_entry_time)
            WHERE workspace_id = ? AND entity_type = ?
            """,
            (_now_iso(), records_fetched, records_upserted,
             last_entry_time, workspace_id, entity_type),
        )

    async def fail_sync(
        self, workspace_id: str, entity_type: str, error: str
    ) -> None:
        await self._db.execute(
            """
            UPDATE sync_log SET
                status = 'failed',
                completed_at = ?,
                error_message = ?
            WHERE workspace_id = ? AND entity_type = ?
            """,
            (_now_iso(), error, workspace_id, entity_type),
        )

    async def get_last_sync(
        self, workspace_id: str, entity_type: str
    ) -> Optional[dict]:
        return await self._db.fetchone(
            "SELECT * FROM sync_log WHERE workspace_id = ? AND entity_type = ?",
            (workspace_id, entity_type),
        )

    async def get_all_sync_status(self, workspace_id: str) -> list[dict]:
        return await self._db.fetchall(
            "SELECT * FROM sync_log WHERE workspace_id = ? ORDER BY entity_type",
            (workspace_id,),
        )
