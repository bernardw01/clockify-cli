"""Orchestrates pushing time entries from SQLite into Fibery Labor Costs."""
import asyncio
from typing import Awaitable, Callable, Optional

from loguru import logger

from clockify_cli.constants import FIBERY_BATCH_SIZE
from clockify_cli.db.database import Database
from clockify_cli.fibery.client import FiberyClient
from clockify_cli.fibery.models import LaborCostPayload, PushProgress

ProgressCallback = Callable[[PushProgress], Awaitable[None]]

_ENTRIES_SQL = """
    SELECT
        te.id,
        te.start_time,
        te.end_time,
        te.duration,
        te.description,
        te.task_id,
        te.project_id,
        te.billable,
        u.id        AS user_id,
        u.name      AS user_name,
        u.email     AS user_email,
        p.name      AS project_name
    FROM time_entries te
    LEFT JOIN users    u ON te.user_id    = u.id
    LEFT JOIN projects p ON te.project_id = p.id
    WHERE te.workspace_id = ?
    ORDER BY te.start_time ASC
"""


class FiberyPushOrchestrator:
    """Reads time entries from SQLite and pushes them to Fibery Labor Costs."""

    def __init__(self, client: FiberyClient, db: Database) -> None:
        self._client = client
        self._db = db

    async def push_all(
        self,
        workspace_id: str,
        on_progress: Optional[ProgressCallback] = None,
    ) -> PushProgress:
        """Push all completed time entries for *workspace_id* to Fibery.

        Strategy: full reconciliation via batch/create-or-update with
        conflict-field = Time Log ID.  Running timers (end_time IS NULL) are
        skipped silently.

        Returns the final PushProgress with counts.
        """
        progress = PushProgress()

        async def _notify() -> None:
            if on_progress:
                await on_progress(progress)

        logger.info(f"Starting Fibery push for workspace {workspace_id}")
        progress.status = "running"
        await _notify()

        # ── Step 0: pre-flight — fetch existing Time Log IDs ──────────────────
        try:
            logger.info("Pre-flight: fetching existing Time Log IDs from Fibery")
            existing_ids = await self._client.get_existing_time_log_ids()
        except Exception as exc:
            progress.status = "error"
            progress.error_message = f"Pre-flight failed: {exc}"
            logger.error(progress.error_message)
            await _notify()
            return progress

        # ── Step 1: load SQLite entries ───────────────────────────────────────
        try:
            rows = await self._db.fetchall(_ENTRIES_SQL, (workspace_id,))
        except Exception as exc:
            progress.status = "error"
            progress.error_message = f"DB read failed: {exc}"
            logger.error(progress.error_message)
            await _notify()
            return progress

        # Separate complete vs running-timer entries
        complete_rows = [r for r in rows if r["end_time"]]
        running_count = len(rows) - len(complete_rows)

        progress.total = len(complete_rows)
        progress.skipped = running_count
        progress.created = sum(1 for r in complete_rows if r["id"] not in existing_ids)
        progress.updated = sum(1 for r in complete_rows if r["id"] in existing_ids)

        if running_count:
            logger.info(f"Skipping {running_count} running-timer entries (no end_time)")
        logger.info(
            f"Loaded {len(complete_rows)} completed entries from SQLite "
            f"({progress.created} new, {progress.updated} to update)"
        )
        await _notify()

        if progress.total == 0:
            progress.status = "done"
            await _notify()
            return progress

        # ── Step 2: build payloads ────────────────────────────────────────────
        payloads = [_build_payload(row) for row in complete_rows]

        # ── Step 3: batch upsert ──────────────────────────────────────────────
        for batch_start in range(0, len(payloads), FIBERY_BATCH_SIZE):
            batch = payloads[batch_start : batch_start + FIBERY_BATCH_SIZE]
            entities = [p.to_fibery_entity() for p in batch]
            try:
                count = await self._client.batch_upsert_labor_costs(entities)
                progress.pushed += count
                logger.debug(
                    f"Batch {batch_start // FIBERY_BATCH_SIZE + 1}: "
                    f"pushed {count} entries "
                    f"(total {progress.pushed}/{progress.total})"
                )
            except Exception as exc:
                progress.errors += len(batch)
                logger.error(f"Batch upsert error: {exc}")
            await _notify()
            # Brief yield so the event loop stays responsive
            await asyncio.sleep(0)

        progress.status = "done" if progress.errors == 0 else "error"
        if progress.errors:
            progress.error_message = f"{progress.errors} entries failed to push"
        logger.info(
            f"Fibery push complete: {progress.created} new, {progress.updated} updated, "
            f"{progress.skipped} skipped, {progress.errors} errors"
        )
        await _notify()
        return progress


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_payload(row: dict) -> LaborCostPayload:
    """Map a SQLite row (with joined user/project fields) to a LaborCostPayload."""
    duration: Optional[int] = row["duration"]
    hours: Optional[float] = round(duration / 3600.0, 4) if duration else None

    return LaborCostPayload(
        time_log_id=row["id"],
        start_dt=row["start_time"],
        end_dt=row["end_time"],
        seconds=duration,
        hours=hours,
        task=row["description"] or None,
        task_id=row["task_id"] or None,
        project_id=row["project_id"] or None,
        billable="Yes" if row["billable"] else "No",
        user_id_text=row["user_email"] or None,
        user_name=row["user_name"] or None,
        project_name=row["project_name"] or None,
    )
