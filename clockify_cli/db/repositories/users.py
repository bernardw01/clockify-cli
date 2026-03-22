"""Repository for the users table."""
from clockify_cli.db.database import Database


class UserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_many(self, users: list[dict], workspace_id: str) -> int:
        if not users:
            return 0
        sql = """
            INSERT OR REPLACE INTO users
                (id, workspace_id, name, email, status, role, avatar_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        await self._db.executemany(sql, [
            (
                u["id"],
                workspace_id,
                u.get("name", ""),
                u.get("email"),
                u.get("status"),
                u.get("role"),
                u.get("profilePicture") or u.get("avatar_url"),
            )
            for u in users
        ])
        return len(users)

    async def get_all(self, workspace_id: str) -> list[dict]:
        return await self._db.fetchall(
            "SELECT * FROM users WHERE workspace_id = ? ORDER BY name",
            (workspace_id,),
        )

    async def get_by_id(self, user_id: str) -> dict | None:
        return await self._db.fetchone(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
