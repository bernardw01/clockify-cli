"""Orchestrates pushing time entries from SQLite into Fibery Labor Costs."""
import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from loguru import logger

from clockify_cli.constants import FIBERY_BATCH_SIZE
from clockify_cli.db.database import Database
from clockify_cli.fibery.client import FiberyClient
from clockify_cli.fibery.models import LaborCostPayload, PushProgress

ProgressCallback = Callable[[PushProgress], Awaitable[None]]

# Fetch entries where workspace matches AND (first push OR record fetched after last push).
# The nullable parameter pattern (? IS NULL OR col > ?) lets us pass None for a full push.
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
      AND (? IS NULL OR te.fetched_at > ?)
    ORDER BY te.start_time ASC
"""

_LOAD_PUSH_LOG_SQL = """
    SELECT last_pushed_at FROM fibery_push_log WHERE workspace_id = ?
"""

_UPSERT_PUSH_LOG_SQL = """
    INSERT INTO fibery_push_log(workspace_id, last_pushed_at)
    VALUES (?, ?)
    ON CONFLICT(workspace_id) DO UPDATE SET last_pushed_at = excluded.last_pushed_at
"""


class FiberyPushOrchestrator:
    """Reads time entries from SQLite and pushes them to Fibery Labor Costs.

    Incremental mode: only entries whose ``fetched_at`` timestamp is newer than
    ``fibery_push_log.last_pushed_at`` are sent.  On the very first push (no log
    row yet) all completed entries are sent.  The log is written only when the
    push finishes with zero errors so a partial failure triggers a full retry on
    the next run.
    """

    def __init__(self, client: FiberyClient, db: Database) -> None:
        self._client = client
        self._db = db

    async def push_all(
        self,
        workspace_id: str,
        on_progress: Optional[ProgressCallback] = None,
    ) -> PushProgress:
        """Push completed time entries for *workspace_id* to Fibery.

        Returns the final PushProgress with counts.
        """
        progress = PushProgress()

        async def _notify() -> None:
            if on_progress:
                await on_progress(progress)

        # Record push-start time before anything else so we can save it on success.
        push_started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # ── Step 0: load last push timestamp ──────────────────────────────────
        row = await self._db.fetchone(_LOAD_PUSH_LOG_SQL, (workspace_id,))
        last_pushed_at: Optional[str] = row["last_pushed_at"] if row else None
        progress.last_pushed_at = last_pushed_at
        progress.is_incremental = last_pushed_at is not None

        mode_desc = (
            f"incremental (since {last_pushed_at})" if last_pushed_at else "full"
        )
        logger.info(f"Starting Fibery push for workspace {workspace_id} — mode: {mode_desc}")
        progress.status = "running"
        await _notify()

        # ── Step 1: pre-flight — fetch existing Time Log IDs ──────────────────
        try:
            logger.info("Pre-flight: fetching existing Time Log IDs from Fibery")
            existing_ids = await self._client.get_existing_time_log_ids()
        except Exception as exc:
            progress.status = "error"
            progress.error_message = f"Pre-flight failed: {exc}"
            logger.error(progress.error_message)
            await _notify()
            return progress

        # ── Step 2: load SQLite entries (filtered by fetched_at) ──────────────
        try:
            rows = await self._db.fetchall(
                _ENTRIES_SQL, (workspace_id, last_pushed_at, last_pushed_at)
            )
        except Exception as exc:
            progress.status = "error"
            progress.error_message = f"DB read failed: {exc}"
            logger.error(progress.error_message)
            await _notify()
            return progress

        # Separate complete entries from still-running timers
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
            # Still save the push log so future runs use the current timestamp
            # as the cut-off, even when there was nothing to push this time.
            await self._db.execute(_UPSERT_PUSH_LOG_SQL, (workspace_id, push_started_at))
            await _notify()
            return progress

        # ── Step 3: build payloads ────────────────────────────────────────────
        payloads = [_build_payload(row) for row in complete_rows]

        # ── Step 4: batch upsert ──────────────────────────────────────────────
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
            await asyncio.sleep(0)

        progress.status = "done" if progress.errors == 0 else "error"
        if progress.errors:
            progress.error_message = f"{progress.errors} entries failed to push"
        else:
            # Only advance the push cursor on a clean run so failed batches are retried.
            await self._db.execute(_UPSERT_PUSH_LOG_SQL, (workspace_id, push_started_at))
            logger.debug(f"Saved last_pushed_at = {push_started_at}")

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
