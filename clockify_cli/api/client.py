"""Async Clockify API client with rate limiting and pagination."""
import asyncio
import math
import time
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

# Max characters of response body to log at DEBUG level
_MAX_BODY_LOG = 500


def _mask_key(key: str) -> str:
    """Show only last 4 chars of API key in logs."""
    if len(key) <= 4:
        return "****"
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def _log_request(method: str, url: str, params: dict | None) -> float:
    """Log the outgoing request and return the start timestamp."""
    param_str = f" params={params}" if params else ""
    logger.debug(f"→ {method} {url}{param_str}")
    return time.monotonic()


def _log_response(method: str, url: str, resp: httpx.Response, started: float) -> None:
    """Log the response with status, elapsed time, and body preview."""
    elapsed_ms = (time.monotonic() - started) * 1000
    body = resp.text
    size = len(resp.content)
    preview = body[:_MAX_BODY_LOG].replace("\n", " ")
    if len(body) > _MAX_BODY_LOG:
        preview += "…"

    level = "DEBUG" if resp.status_code < 400 else "WARNING"
    logger.log(
        level,
        f"← {resp.status_code} {method} {url} "
        f"({elapsed_ms:.0f}ms, {size:,} bytes) | {preview}",
    )


class ClockifyClient:
    """Async HTTP client for the Clockify REST API.

    Usage:
        async with ClockifyClient(api_key) as client:
            workspaces = await client.get_workspaces()
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._http: Optional[httpx.AsyncClient] = None
        self._sem = asyncio.Semaphore(MAX_REQUESTS_PER_SECOND)

    async def __aenter__(self) -> "ClockifyClient":
        logger.info(f"ClockifyClient opening session (key=...{self._api_key[-4:]})")
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
        logger.info("ClockifyClient session closed")

    # ── private helpers ───────────────────────────────────────────────────────

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Use ClockifyClient as an async context manager")
        return self._http

    async def _get(self, path: str, params: dict | None = None) -> Any:
        """Single GET with rate-limit semaphore, full logging, and error mapping."""
        url = f"{BASE_URL}{path}"
        async with self._sem:
            started = _log_request("GET", url, params)
            resp = await self._client.get(path, params=params)
            _log_response("GET", url, resp, started)

        if resp.status_code == 200:
            data = resp.json()
            logger.debug(f"  Parsed {len(data) if isinstance(data, list) else 1} item(s) from {path}")
            return data
        if resp.status_code == 401:
            logger.error(f"Auth failure on {path} — check API key")
            raise AuthError("Invalid or missing API key", status_code=401)
        if resp.status_code == 403:
            logger.error(f"Access forbidden on {path}")
            raise AuthError("Access forbidden — check workspace permissions", status_code=403)
        if resp.status_code == 404:
            logger.warning(f"Not found: {path}")
            raise NotFoundError(f"Resource not found: {path}", status_code=404)
        if resp.status_code == 429:
            logger.warning(f"Rate limit hit on {path}")
            raise RateLimitError("Clockify rate limit exceeded", status_code=429)
        if resp.status_code >= 500:
            logger.error(f"Server error {resp.status_code} on {path}: {resp.text[:200]}")
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
        logger.debug(f"Starting paginated fetch: {path}")
        while True:
            params["page"] = page
            data = await self._get(path, params)
            if not data:
                break
            results.extend(data)
            logger.debug(f"  Page {page}: {len(data)} items (total so far: {len(results)})")
            if len(data) < params["page-size"]:
                break  # last page
            page += 1
        logger.info(f"Paginated fetch complete: {path} → {len(results)} total items")
        return results

    # ── public API methods ────────────────────────────────────────────────────

    async def get_workspaces(self) -> list[Workspace]:
        logger.info("Fetching workspaces")
        data = await self._get("/workspaces")
        workspaces = [Workspace.model_validate(w) for w in data]
        logger.info(f"Found {len(workspaces)} workspace(s): {[w.name for w in workspaces]}")
        return workspaces

    async def get_clients(self, workspace_id: str) -> list[Client]:
        logger.info(f"Fetching clients for workspace {workspace_id}")
        data = await self._get_paginated(
            f"/workspaces/{workspace_id}/clients",
            {"archived": "false"},
        )
        clients = [Client.model_validate(c) for c in data]
        logger.info(f"Fetched {len(clients)} client(s)")
        return clients

    async def get_projects(self, workspace_id: str) -> list[Project]:
        logger.info(f"Fetching projects for workspace {workspace_id}")
        data = await self._get_paginated(
            f"/workspaces/{workspace_id}/projects",
            {"archived": "false"},
        )
        projects = [Project.model_validate(p) for p in data]
        logger.info(f"Fetched {len(projects)} project(s)")
        return projects

    async def get_users(self, workspace_id: str) -> list[WorkspaceUser]:
        logger.info(f"Fetching users for workspace {workspace_id}")
        data = await self._get_paginated(f"/workspaces/{workspace_id}/users")
        users = [WorkspaceUser.model_validate(u) for u in data]
        logger.info(f"Fetched {len(users)} user(s): {[u.name for u in users]}")
        return users

    async def iter_time_entries(
        self,
        workspace_id: str,
        user_id: str,
        start: Optional[str] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> AsyncGenerator[tuple[list[TimeEntry], int, int], None]:
        """Yield (entries_page, current_page, total_pages) for each page."""
        params: dict[str, Any] = {"page-size": page_size}
        if start:
            params["start"] = start
            logger.info(f"Incremental fetch for user {user_id} from {start}")
        else:
            logger.info(f"Full fetch of time entries for user {user_id}")

        page = 1
        total_pages: Optional[int] = None
        path = f"/workspaces/{workspace_id}/user/{user_id}/time-entries"

        while True:
            params["page"] = page
            url = f"{BASE_URL}{path}"

            async with self._sem:
                started = _log_request("GET", url, params)
                resp = await self._client.get(path, params=params)
                _log_response("GET", url, resp, started)

            if resp.status_code == 401:
                logger.error("Auth failure fetching time entries")
                raise AuthError("Invalid or missing API key", status_code=401)
            if resp.status_code == 429:
                logger.warning("Rate limit hit fetching time entries")
                raise RateLimitError("Rate limit exceeded", status_code=429)
            if resp.status_code not in (200, 204):
                raise ClockifyAPIError(
                    f"Unexpected status {resp.status_code}", status_code=resp.status_code
                )

            # Determine total pages from X-Total-Count on first response
            if total_pages is None:
                try:
                    total_count = int(resp.headers.get("X-Total-Count", 0))
                    total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1
                    logger.info(
                        f"Time entries for user {user_id}: "
                        f"{total_count} total, {total_pages} page(s)"
                    )
                except (ValueError, TypeError):
                    total_pages = 1

            data = resp.json() if resp.status_code == 200 else []
            if not data:
                logger.debug(f"No entries on page {page} for user {user_id} — done")
                break

            entries = [TimeEntry.model_validate(e) for e in data]
            logger.debug(
                f"User {user_id} page {page}/{total_pages}: {len(entries)} entries"
            )
            yield entries, page, total_pages

            if len(data) < page_size:
                logger.debug(f"Partial page ({len(data)} < {page_size}) — last page reached")
                break
            page += 1
