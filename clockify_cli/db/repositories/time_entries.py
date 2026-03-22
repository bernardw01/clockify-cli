"""Repository for the time_entries table."""
import json
from typing import Optional

from clockify_cli.db.database import Database


class TimeEntryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_many(self, entries: list[dict], workspace_id: str) -> int:
        """Bulk upsert time entries. Returns count of rows written."""
        if not entries:
            return 0
        sql = """
            INSERT OR REPLACE INTO time_entries
                (id, workspace_id, user_id, project_id, description,
                 start_time, end_time, duration, billable, is_locked,
                 task_id, tag_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
