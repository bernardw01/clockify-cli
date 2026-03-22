"""Repository for the clients table."""
from clockify_cli.db.database import Database


class ClientRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_many(self, clients: list[dict], workspace_id: str) -> int:
        if not clients:
            return 0
        sql = """
            INSERT OR REPLACE INTO clients (id, workspace_id, name, archived)
            VALUES (?, ?, ?, ?)
        """
        await self._db.executemany(sql, [
            (c["id"], workspace_id, c["name"], int(c.get("archived", False)))
            for c in clients
        ])
        return len(clients)

    async def get_all(self, workspace_id: str) -> list[dict]:
        return await self._db.fetchall(
            "SELECT * FROM clients WHERE workspace_id = ? ORDER BY name",
            (workspace_id,),
        )
