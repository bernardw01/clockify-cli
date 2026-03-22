"""Repository for the projects table."""
from clockify_cli.db.database import Database


class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_many(self, projects: list[dict], workspace_id: str) -> int:
        if not projects:
            return 0
        sql = """
            INSERT OR REPLACE INTO projects
                (id, workspace_id, client_id, name, color, archived, billable, public)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        await self._db.executemany(sql, [
            (
                p["id"],
                workspace_id,
                p.get("clientId") or p.get("client_id"),
                p["name"],
                p.get("color"),
                int(p.get("archived", False)),
                int(p.get("billable", False)),
                int(p.get("public", False)),
            )
            for p in projects
        ])
        return len(projects)

    async def get_all(self, workspace_id: str, include_archived: bool = False) -> list[dict]:
        sql = "SELECT * FROM projects WHERE workspace_id = ?"
        params: tuple = (workspace_id,)
        if not include_archived:
            sql += " AND archived = 0"
        sql += " ORDER BY name"
        return await self._db.fetchall(sql, params)

    async def get_by_id(self, project_id: str) -> dict | None:
        return await self._db.fetchone(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        )
