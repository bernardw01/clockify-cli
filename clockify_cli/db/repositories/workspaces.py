"""Repository for the workspaces table."""
from clockify_cli.db.database import Database


class WorkspaceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_many(self, workspaces: list[dict]) -> int:
        if not workspaces:
            return 0
        sql = """
            INSERT OR REPLACE INTO workspaces (id, name, currency_code, image_url)
            VALUES (?, ?, ?, ?)
        """
        await self._db.executemany(sql, [
            (w["id"], w["name"], w.get("currency_code"), w.get("image_url"))
            for w in workspaces
        ])
        return len(workspaces)

    async def get_all(self) -> list[dict]:
        return await self._db.fetchall("SELECT * FROM workspaces ORDER BY name")

    async def get_by_id(self, workspace_id: str) -> dict | None:
        return await self._db.fetchone(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        )
