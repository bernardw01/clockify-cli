"""Orchestrates pushing time entries from SQLite into Fibery Labor Costs."""
import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from loguru import logger

from clockify_cli.constants import FIBERY_BATCH_SIZE
from clockify_cli.db.database import Database
from clockify_cli.fibery.client import FiberyClient
from clockify_cli.fibery.models import (
    ClockifyUpdateLogResult,
    LaborCostPayload,
    PushProgress,
)

ProgressCallback = Callable[[PushProgress], Awaitable[None]]

# Fetch entries where workspace matches AND approval status is push-eligible
# (PENDING or APPROVED) AND (first push OR record fetched at/after checkpoint).
# The nullable parameter pattern (? IS NULL OR col >= ?) lets us pass None for a full push.
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
        te.approval_status,
        u.id        AS user_id,
        u.name      AS user_name,
        u.email     AS user_email,
        p.name      AS project_name
    FROM time_entries te
    LEFT JOIN users    u ON te.user_id    = u.id
    LEFT JOIN projects p ON te.project_id = p.id
    WHERE te.workspace_id = ?
      AND te.approval_status IN ('PENDING', 'APPROVED')
      AND (? IS NULL OR te.fetched_at >= ?)
    ORDER BY te.start_time ASC
"""


class FiberyPushOrchestrator:
    """Reads time entries from SQLite and pushes them to Fibery Labor Costs.

    Incremental mode reads the checkpoint from Fibery Clockify Update Log and
    sends only entries whose ``fetched_at`` timestamp is at/after that value.
    If no Fibery checkpoint exists yet, incremental push is blocked and the
    caller is advised to run a full refresh first.
    """

    def __init__(self, client: FiberyClient, db: Database) -> None:
        self._client = client
        self._db = db

    async def push_all(
        self,
        workspace_id: str,
        replace_all: bool = False,
        on_progress: Optional[ProgressCallback] = None,
    ) -> PushProgress:
        """Push completed time entries for *workspace_id* to Fibery.

        Returns the final PushProgress with counts.
        """
        progress = PushProgress()

        async def _notify() -> None:
            if on_progress:
                await on_progress(progress)

        # Record push-start time for this run and for log entry summary.
        push_started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # ── Step 0: load last run timestamp from Fibery Clockify Update Log ───
        last_pushed_at: Optional[str]
        if replace_all:
            last_pushed_at = None
        else:
            try:
                last_pushed_at = await self._client.get_last_clockify_update_run_at()
            except Exception as exc:
                progress.status = "error"
                progress.error_message = f"Clockify Update Log query failed: {exc}"
                logger.error(progress.error_message)
                await _notify()
                return progress
            if not last_pushed_at:
                progress.status = "error"
                progress.error_message = (
                    "Clockify Update Log is empty. Recommendation: run a full data "
                    "refresh before running incremental push."
                )
                logger.warning(progress.error_message)
                await _notify()
                return progress
        progress.last_pushed_at = last_pushed_at
        progress.is_incremental = last_pushed_at is not None

        mode_desc = (
            f"incremental (since {last_pushed_at})" if last_pushed_at else "full"
        )
        if replace_all:
            mode_desc = "replace-all (delete Fibery Labor Costs, then full push)"
        logger.info(f"Starting Fibery push for workspace {workspace_id} — mode: {mode_desc}")
        progress.status = "running"
        await _notify()

        # ── Step 1: optional replace-all purge ─────────────────────────────────
        if replace_all:
            try:
                entity_ids = await self._client.get_labor_cost_entity_ids()
                if entity_ids:
                    logger.info(f"Replace-all mode: deleting {len(entity_ids)} Fibery Labor Cost rows")
                    progress.phase = "deleting"
                    progress.total = len(entity_ids)
                    progress.pushed = 0
                    await _notify()

                    async def _on_delete_progress(deleted: int, total: int) -> None:
                        progress.total = total
                        progress.pushed = deleted
                        await _notify()

                    await self._client.delete_labor_cost_entities(
                        entity_ids,
                        on_progress=_on_delete_progress,
                    )
                else:
                    logger.info("Replace-all mode: Fibery Labor Costs already empty")
            except Exception as exc:
                progress.status = "error"
                progress.error_message = f"Replace-all delete failed: {exc}"
                logger.error(progress.error_message)
                await _notify()
                return progress
            progress.phase = "pushing"
            progress.total = 0
            progress.pushed = 0

        # ── Step 2: pre-flight — fetch existing Time Log IDs ──────────────────
        try:
            logger.info("Pre-flight: fetching existing Time Log IDs from Fibery")
            existing_ids = await self._client.get_existing_time_log_ids()
        except Exception as exc:
            progress.status = "error"
            progress.error_message = f"Pre-flight failed: {exc}"
            logger.error(progress.error_message)
            await _notify()
            return progress

        # ── Step 3: load SQLite entries (filtered by fetched_at) ──────────────
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
            completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            await self._append_update_log(
                workspace_id=workspace_id,
                push_started_at=push_started_at,
                completed_at=completed_at,
                progress=progress,
            )
            await _notify()
            return progress

        # ── Step 4: build payloads ────────────────────────────────────────────
        payloads = [_build_payload(row) for row in complete_rows]

        # ── Step 5: batch upsert ──────────────────────────────────────────────
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
        completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self._append_update_log(
            workspace_id=workspace_id,
            push_started_at=push_started_at,
            completed_at=completed_at,
            progress=progress,
        )

        logger.info(
            f"Fibery push complete: {progress.created} new, {progress.updated} updated, "
            f"{progress.skipped} skipped, {progress.errors} errors"
        )
        await _notify()
        return progress

    async def _append_update_log(
        self,
        workspace_id: str,
        push_started_at: str,
        completed_at: str,
        progress: PushProgress,
    ) -> None:
        """Write one run summary row into Fibery Clockify Update Log."""
        try:
            await self._client.append_clockify_update_log(
                ClockifyUpdateLogResult(
                    workspace_id=workspace_id,
                    started_at=push_started_at,
                    completed_at=completed_at,
                    status=progress.status,
                    total=progress.total,
                    pushed=progress.pushed,
                    created=progress.created,
                    updated=progress.updated,
                    skipped=progress.skipped,
                    errors=progress.errors,
                )
            )
        except Exception as exc:
            progress.status = "error"
            progress.error_message = f"Clockify Update Log write failed: {exc}"
            logger.error(progress.error_message)


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
        approval_status=row["approval_status"] or "NOT_SUBMITTED",
        user_id_text=row["user_email"] or None,
        user_name=row["user_name"] or None,
        project_name=row["project_name"] or None,
    )
