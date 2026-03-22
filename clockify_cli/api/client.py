"""Async Clockify API client with rate limiting and pagination."""
import asyncio
from typing import Any, AsyncGenerator, Optional

import httpx
from loguru import logger

from clockify_cli.api.exceptions import (
    AuthError,
    ClockifyAPIError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from clockify_cli.api.models import Client, Project, TimeEntry, Workspace, WorkspaceUser
from clockify_cli.constants import BASE_URL, DEFAULT_PAGE_SIZE, MAX_REQUESTS_PER_SECOND


class ClockifyClient:
    """Async HTTP client for the Clockify REST API.

    Usage:
        async with ClockifyClient(api_key) as client:
            workspaces = await client.get_workspaces()
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._http: Optional[httpx.AsyncClient] = None
        # Semaphore limits concurrent in-flight requests (not strictly per-second,
        # but prevents thundering-herd; combined with small sleep it stays safe).
        self._sem = asyncio.Semaphore(MAX_REQUESTS_PER_SECOND)

    async def __aenter__(self) -> "ClockifyClient":
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "X-Api-Key": self._api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── private helpers ───────────────────────────────────────────────────────

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Use ClockifyClient as an async context manager")
        return self._http

    async def _get(self, path: str, params: dict | None = None) -> Any:
        """Single GET with rate-limit semaphore and error mapping."""
        async with self._sem:
            logger.debug(f"GET {path} params={params}")
            resp = await self._client.get(path, params=params)

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 401:
            raise AuthError("Invalid or missing API key", status_code=401)
        if resp.status_code == 403:
            raise AuthError("Access forbidden — check workspace permissions", status_code=403)
        if resp.status_code == 404:
            raise NotFoundError(f"Resource not found: {path}", status_code=404)
        if resp.status_code == 429:
            raise RateLimitError("Clockify rate limit exceeded", status_code=429)
        if resp.status_code >= 500:
            raise ServerError(
                f"Clockify server error {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        raise ClockifyAPIError(
            f"Unexpected response {resp.status_code}: {resp.text[:200]}",
            status_code=resp.status_code,
        )

    async def _get_paginated(self, path: str, params: dict | None = None) -> list[Any]:
        """Collect all pages from a paginated endpoint into a single list."""
        params = dict(params or {})
        params.setdefault("page-size", DEFAULT_PAGE_SIZE)
        page = 1
        results: list[Any] = []
        while True:
            params["page"] = page
            data = await self._get(path, params)
            if not data:
                break
            results.extend(data)
            if len(data) < params["page-size"]:
                break  # last page
            page += 1
        return results

    # ── public API methods ────────────────────────────────────────────────────

    async def get_workspaces(self) -> list[Workspace]:
        data = await self._get("/workspaces")
        return [Workspace.model_validate(w) for w in data]

    async def get_clients(self, workspace_id: str) -> list[Client]:
        data = await self._get_paginated(
            f"/workspaces/{workspace_id}/clients",
            {"archived": "false"},
        )
        return [Client.model_validate(c) for c in data]

    async def get_projects(self, workspace_id: str) -> list[Project]:
        data = await self._get_paginated(
            f"/workspaces/{workspace_id}/projects",
            {"archived": "false"},
        )
        return [Project.model_validate(p) for p in data]

    async def get_users(self, workspace_id: str) -> list[WorkspaceUser]:
        data = await self._get_paginated(f"/workspaces/{workspace_id}/users")
        return [WorkspaceUser.model_validate(u) for u in data]

    async def iter_time_entries(
        self,
        workspace_id: str,
        user_id: str,
        start: Optional[str] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> AsyncGenerator[tuple[list[TimeEntry], int, int], None]:
        """Yield (entries_page, current_page, total_pages) for each page.

        Uses the ``Last-Page`` response header to determine total pages.
        If ``start`` is provided, only entries with start >= that time are fetched
        (enabling incremental sync).
        """
        params: dict[str, Any] = {"page-size": page_size}
        if start:
            params["start"] = start

        page = 1
        total_pages: Optional[int] = None

        while True:
            params["page"] = page
            path = f"/workspaces/{workspace_id}/user/{user_id}/time-entries"

            async with self._sem:
                logger.debug(f"GET {path} page={page}")
                resp = await self._client.get(path, params=params)

            if resp.status_code == 401:
                raise AuthError("Invalid or missing API key", status_code=401)
            if resp.status_code == 429:
                raise RateLimitError("Rate limit exceeded", status_code=429)
            if resp.status_code not in (200, 204):
                raise ClockifyAPIError(
                    f"Unexpected status {resp.status_code}", status_code=resp.status_code
                )

            # Parse total pages from header on first response
            if total_pages is None:
                try:
                    total_pages = int(resp.headers.get("X-Total-Count", 0))
                    if total_pages > 0:
                        import math
                        total_pages = math.ceil(total_pages / page_size)
                    else:
                        total_pages = 1
                except (ValueError, TypeError):
                    total_pages = 1

            data = resp.json() if resp.status_code == 200 else []
            if not data:
                break

            entries = [TimeEntry.model_validate(e) for e in data]
            yield entries, page, total_pages

            if len(data) < page_size:
                break  # received a partial page → done
            page += 1
