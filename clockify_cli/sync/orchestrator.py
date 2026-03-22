"""Sync orchestrator: coordinates fetching all entities from Clockify into SQLite."""
import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from loguru import logger

from clockify_cli.api.client import ClockifyClient
from clockify_cli.api.exceptions import ClockifyAPIError
from clockify_cli.db.database import Database
from clockify_cli.db.repositories.clients import ClientRepository
from clockify_cli.db.repositories.projects import ProjectRepository
from clockify_cli.db.repositories.sync_log import SyncLogRepository
from clockify_cli.db.repositories.time_entries import TimeEntryRepository
from clockify_cli.db.repositories.users import UserRepository
from clockify_cli.db.repositories.workspaces import WorkspaceRepository
from clockify_cli.sync.progress import EntityProgress, SyncProgress

ProgressCallback = Callable[[SyncProgress], Awaitable[None]]


class SyncOrchestrator:
    """Coordinates sync of all entity types for a single workspace."""

    def __init__(self, client: ClockifyClient, db: Database) -> None:
        self._client = client
        self._db = db
        self._workspaces = WorkspaceRepository(db)
        self._clients = ClientRepository(db)
        self._projects = ProjectRepository(db)
        self._users = UserRepository(db)
        self._entries = TimeEntryRepository(db)
        self._sync_log = SyncLogRepository(db)

    async def sync_all(
        self,
        workspace_id: str,
        incremental: bool = True,
        on_progress: Optional[ProgressCallback] = None,
    ) -> SyncProgress:
        """Run a full sync: clients → projects → users → time_entries.

        Args:
            workspace_id: Clockify workspace to sync.
            incremental: If True, only fetch entries newer than last sync.
            on_progress: Async callback invoked after each step/page update.

        Returns:
            Completed SyncProgress with final counts and any errors.
        """
        progress = SyncProgress(workspace_id=workspace_id, incremental=incremental)

        async def _notify() -> None:
            if on_progress:
                await on_progress(progress)

        logger.info(
            f"Starting {'incremental' if incremental else 'full'} sync "
            f"for workspace {workspace_id}"
        )

        # 1. Clients
        await self._sync_entity(
            progress, "clients", _notify,
            self._sync_clients(workspace_id, progress),
        )

        # 2. Projects
        await self._sync_entity(
            progress, "projects", _notify,
            self._sync_projects(workspace_id, progress),
        )

        # 3. Users
        await self._sync_entity(
            progress, "users", _notify,
            self._sync_users(workspace_id, progress),
        )

        # 4. Time entries (most expensive — per-user pagination)
        await self._sync_entity(
            progress, "time_entries", _notify,
            self._sync_time_entries(workspace_id, incremental, progress, _notify),
        )

        progress.completed_at = datetime.now(timezone.utc).isoformat()
        await _notify()
        logger.info(
            f"Sync complete: {progress.total_records} total records, "
            f"errors={progress.has_errors}"
        )
        return progress

    # ── entity sync helpers ───────────────────────────────────────────────────

    async def _sync_entity(
        self,
        progress: SyncProgress,
        entity_type: str,
        notify: Callable[[], Awaitable[None]],
        coro: object,  # coroutine
    ) -> None:
        ep: EntityProgress = progress.entities[entity_type]  # type: ignore[index]
        ep.status = "running"
        await notify()
        try:
            await coro  # type: ignore[misc]
            ep.status = "done"
        except ClockifyAPIError as exc:
            ep.status = "error"
            ep.error = str(exc)
            await self._sync_log.fail_sync(progress.workspace_id, entity_type, str(exc))
            logger.error(f"Sync failed for {entity_type}: {exc}")
        await notify()

    async def _sync_clients(self, workspace_id: str, progress: SyncProgress) -> None:
        ep = progress.entities["clients"]
        await self._sync_log.start_sync(workspace_id, "clients")
        clients = await self._client.get_clients(workspace_id)
        ep.records_fetched = len(clients)
        count = await self._clients.upsert_many(
            [c.to_db_dict() for c in clients], workspace_id
        )
        ep.records_upserted = count
        ep.current_page = ep.total_pages = 1
        await self._sync_log.complete_sync(workspace_id, "clients", len(clients), count)
        logger.debug(f"Synced {count} clients")

    async def _sync_projects(self, workspace_id: str, progress: SyncProgress) -> None:
        ep = progress.entities["projects"]
        await self._sync_log.start_sync(workspace_id, "projects")
        projects = await self._client.get_projects(workspace_id)
        ep.records_fetched = len(projects)
        count = await self._projects.upsert_many(
            [p.to_db_dict() for p in projects], workspace_id
        )
        ep.records_upserted = count
        ep.current_page = ep.total_pages = 1
        await self._sync_log.complete_sync(workspace_id, "projects", len(projects), count)
        logger.debug(f"Synced {count} projects")

    async def _sync_users(self, workspace_id: str, progress: SyncProgress) -> None:
        ep = progress.entities["users"]
        await self._sync_log.start_sync(workspace_id, "users")
        users = await self._client.get_users(workspace_id)
        ep.records_fetched = len(users)
        count = await self._users.upsert_many(
            [u.to_db_dict() for u in users], workspace_id
        )
        ep.records_upserted = count
        ep.current_page = ep.total_pages = 1
        await self._sync_log.complete_sync(workspace_id, "users", len(users), count)
        logger.debug(f"Synced {count} users")

    async def _sync_time_entries(
        self,
        workspace_id: str,
        incremental: bool,
        progress: SyncProgress,
        notify: Callable[[], Awaitable[None]],
    ) -> None:
        ep = progress.entities["time_entries"]
        await self._sync_log.start_sync(workspace_id, "time_entries")

        users = await self._users.get_all(workspace_id)
        if not users:
            logger.warning("No users found — skipping time entry sync")
            ep.current_page = ep.total_pages = 1
            await self._sync_log.complete_sync(workspace_id, "time_entries", 0, 0)
            return

        total_fetched = 0
        total_upserted = 0
        last_entry_time: Optional[str] = None

        for user in users:
            user_id = user["id"]
            start: Optional[str] = None
            if incremental:
                start = await self._entries.get_latest_entry_time(workspace_id, user_id)
                if start:
                    logger.debug(f"Incremental sync for user {user_id} from {start}")

            async for page_entries, page_num, total_pages in self._client.iter_time_entries(
                workspace_id, user_id, start=start
            ):
                # Inject user_id (some endpoints omit it in response body)
                dicts = []
                for e in page_entries:
                    d = e.to_db_dict()
                    if not d.get("userId"):
                        d["userId"] = user_id
                    dicts.append(d)

                upserted = await self._entries.upsert_many(dicts, workspace_id)
                total_fetched += len(page_entries)
                total_upserted += upserted

                # Track the most recent start_time across all pages/users
                if page_entries:
                    page_latest = page_entries[0].time_interval.start
                    if last_entry_time is None or page_latest > last_entry_time:
                        last_entry_time = page_latest

                # Update progress for TUI
                ep.records_fetched = total_fetched
                ep.records_upserted = total_upserted
                ep.current_page = page_num
                ep.total_pages = max(ep.total_pages, total_pages * len(users))
                await notify()

                # Brief yield to keep the event loop responsive
                await asyncio.sleep(0)

        await self._sync_log.complete_sync(
            workspace_id, "time_entries",
            total_fetched, total_upserted, last_entry_time
        )
        logger.info(f"Synced {total_upserted} time entries ({total_fetched} fetched)")
