"""Repository for the time_entries table."""
import json
from typing import Optional

from clockify_cli.db.database import Database


class TimeEntryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_many(self, entries: list[dict], workspace_id: str) -> int:
        """Bulk upsert time entries. Returns count of rows written.

        fetched_at is only updated when the entry content actually changed so that
        the Fibery incremental-push filter (fetched_at > last_pushed_at) does not
        flag every entry as modified after every Clockify sync.
        """
        if not entries:
            return 0
        sql = """
            INSERT INTO time_entries
                (id, workspace_id, user_id, project_id, description,
                 start_time, end_time, duration, billable, is_locked,
                 task_id, tag_ids, approval_status, approver_id, approver_name, approved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                user_id      = excluded.user_id,
                project_id   = excluded.project_id,
                description  = excluded.description,
                start_time   = excluded.start_time,
                end_time     = excluded.end_time,
                duration     = excluded.duration,
                billable     = excluded.billable,
                is_locked    = excluded.is_locked,
                task_id      = excluded.task_id,
                tag_ids      = excluded.tag_ids,
                approval_status = excluded.approval_status,
                approver_id = excluded.approver_id,
                approver_name = excluded.approver_name,
                approved_at = excluded.approved_at,
                fetched_at   = CASE
                    WHEN time_entries.project_id   IS NOT excluded.project_id
                      OR time_entries.description  IS NOT excluded.description
                      OR time_entries.start_time   IS NOT excluded.start_time
                      OR time_entries.end_time     IS NOT excluded.end_time
                      OR time_entries.duration     IS NOT excluded.duration
                      OR time_entries.billable     IS NOT excluded.billable
                      OR time_entries.is_locked    IS NOT excluded.is_locked
                      OR time_entries.task_id      IS NOT excluded.task_id
                      OR time_entries.tag_ids      IS NOT excluded.tag_ids
                    THEN datetime('now')
                    ELSE time_entries.fetched_at
                END
        """
        rows = []
        for e in entries:
            interval = e.get("timeInterval") or e.get("time_interval") or {}
            start = interval.get("start") or e.get("start_time", "")
            end = interval.get("end") or e.get("end_time")
            # Convert ISO 8601 duration (PT1H30M) to seconds if present
            raw_dur = interval.get("duration") or e.get("duration")
            duration = _parse_duration(raw_dur) if isinstance(raw_dur, str) else raw_dur
            tag_ids = e.get("tagIds") or e.get("tag_ids") or []
            rows.append((
                e["id"],
                workspace_id,
                e.get("userId") or e.get("user_id", ""),
                e.get("projectId") or e.get("project_id"),
                e.get("description"),
                start,
                end,
                duration,
                int(e.get("billable", False)),
                int(e.get("isLocked") or e.get("is_locked", False)),
                e.get("taskId") or e.get("task_id"),
                json.dumps(tag_ids) if tag_ids else None,
                e.get("approvalStatus") or e.get("approval_status") or "NOT_SUBMITTED",
                e.get("approverId") or e.get("approver_id"),
                e.get("approverName") or e.get("approver_name"),
                e.get("approvedAt") or e.get("approved_at"),
            ))
        await self._db.executemany(sql, rows)
        return len(rows)

    async def get_latest_entry_time(
        self, workspace_id: str, user_id: Optional[str] = None
    ) -> Optional[str]:
        """Return start_time of the most recent entry for incremental sync."""
        if user_id:
            row = await self._db.fetchone(
                "SELECT MAX(start_time) AS t FROM time_entries "
                "WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            )
        else:
            row = await self._db.fetchone(
                "SELECT MAX(start_time) AS t FROM time_entries WHERE workspace_id = ?",
                (workspace_id,),
            )
        return row["t"] if row else None

    async def search(
        self,
        workspace_id: str,
        query: str = "",
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        start_after: Optional[str] = None,
        start_before: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        conditions = ["te.workspace_id = ?"]
        params: list = [workspace_id]

        if query:
            conditions.append("te.description LIKE ?")
            params.append(f"%{query}%")
        if project_id:
            conditions.append("te.project_id = ?")
            params.append(project_id)
        if user_id:
            conditions.append("te.user_id = ?")
            params.append(user_id)
        if start_after:
            conditions.append("te.start_time >= ?")
            params.append(start_after)
        if start_before:
            conditions.append("te.start_time <= ?")
            params.append(start_before)

        where = " AND ".join(conditions)
        sql = f"""
            SELECT
                te.id, te.start_time, te.end_time, te.duration,
                te.description, te.billable, te.is_locked,
                te.approver_id, te.approver_name, te.approved_at,
                u.name AS user_name,
                p.name AS project_name,
                p.color AS project_color
            FROM time_entries te
            LEFT JOIN users u ON te.user_id = u.id
            LEFT JOIN projects p ON te.project_id = p.id
            WHERE {where}
            ORDER BY te.start_time DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        return await self._db.fetchall(sql, tuple(params))

    async def count(self, workspace_id: str) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM time_entries WHERE workspace_id = ?",
            (workspace_id,),
        )
        return row["n"] if row else 0

    async def get_by_id(self, workspace_id: str, entry_id: str) -> dict | None:
        """Return one time entry with joined user/project fields."""
        return await self._db.fetchone(
            """
            SELECT
                te.id,
                te.workspace_id,
                te.user_id,
                te.project_id,
                te.description,
                te.start_time,
                te.end_time,
                te.duration,
                te.billable,
                te.is_locked,
                te.task_id,
                te.approval_status,
                te.approver_id,
                te.approver_name,
                te.approved_at,
                te.fetched_at,
                u.name AS user_name,
                u.email AS user_email,
                p.name AS project_name
            FROM time_entries te
            LEFT JOIN users u ON te.user_id = u.id
            LEFT JOIN projects p ON te.project_id = p.id
            WHERE te.workspace_id = ? AND te.id = ?
            LIMIT 1
            """,
            (workspace_id, entry_id),
        )

    async def get_approval_status_counts(self, workspace_id: str) -> dict[str, int]:
        """Return counts by approval_status for one workspace."""
        rows = await self._db.fetchall(
            "SELECT approval_status, COUNT(*) AS n "
            "FROM time_entries WHERE workspace_id = ? "
            "GROUP BY approval_status",
            (workspace_id,),
        )
        counts = {"NOT_SUBMITTED": 0, "PENDING": 0, "APPROVED": 0}
        for row in rows:
            status = row.get("approval_status")
            if status:
                counts[status] = int(row.get("n") or 0)
        return counts

    async def reset_approval_status(self, workspace_id: str) -> int:
        """Set all workspace entries to NOT_SUBMITTED before enrichment."""
        cursor = await self._db.execute(
            "UPDATE time_entries "
            "SET approval_status = 'NOT_SUBMITTED', "
            "approver_id = NULL, approver_name = NULL, approved_at = NULL "
            "WHERE workspace_id = ?",
            (workspace_id,),
        )
        return int(cursor.rowcount or 0)

    async def set_approval_status_for_ids(
        self,
        workspace_id: str,
        entry_ids: set[str],
        approval_status: str,
    ) -> int:
        """Apply one approval status to a set of entry IDs."""
        if not entry_ids:
            return 0

        updated = 0
        ids = list(entry_ids)
        chunk_size = 900
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i:i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            sql = (
                "UPDATE time_entries "
                f"SET approval_status = ? WHERE workspace_id = ? AND id IN ({placeholders})"
            )
            params = (approval_status, workspace_id, *chunk)
            cursor = await self._db.execute(sql, params)
            updated += int(cursor.rowcount or 0)
        return updated

    async def apply_approval_details(
        self,
        workspace_id: str,
        details_by_entry_id: dict[str, dict[str, Optional[str]]],
    ) -> int:
        """Apply status + approver metadata for specific entries."""
        if not details_by_entry_id:
            return 0
        sql = (
            "UPDATE time_entries SET "
            "approval_status = ?, approver_id = ?, approver_name = ?, approved_at = ? "
            "WHERE workspace_id = ? AND id = ?"
        )
        params: list[tuple] = []
        for entry_id, details in details_by_entry_id.items():
            params.append((
                details.get("status") or "NOT_SUBMITTED",
                details.get("approver_id"),
                details.get("approver_name"),
                details.get("approved_at"),
                workspace_id,
                entry_id,
            ))
        await self._db.executemany(sql, params)
        return len(params)


def _parse_duration(iso_duration: str) -> Optional[int]:
    """Convert ISO 8601 duration string (e.g. PT1H30M15S) to seconds."""
    if not iso_duration or not iso_duration.startswith("PT"):
        return None
    s = iso_duration[2:]  # strip "PT"
    total = 0
    current = ""
    for ch in s:
        if ch.isdigit():
            current += ch
        elif ch == "H":
            total += int(current) * 3600
            current = ""
        elif ch == "M":
            total += int(current) * 60
            current = ""
        elif ch == "S":
            total += int(current)
            current = ""
    return total
