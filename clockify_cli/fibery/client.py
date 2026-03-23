"""Async Fibery Entity Commands API client."""
import asyncio
import time
import uuid
from typing import Any, Optional

import httpx
from loguru import logger

from clockify_cli.api.exceptions import AuthError, ClockifyAPIError, RateLimitError
from clockify_cli.constants import (
    FIBERY_AGREEMENTS_TYPE,
    FIBERY_BATCH_SIZE,
    FIBERY_CLOCKIFY_USERS_TYPE,
    FIBERY_COMMANDS_PATH,
    FIBERY_LABOR_COSTS_TYPE,
    FIBERY_MAX_CONCURRENT,
)

_MAX_BODY_LOG = 300


def _mask_key(key: str) -> str:
    """Show only last 4 characters of API key in logs."""
    if len(key) <= 4:
        return "****"
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


class FiberyClient:
    """Async HTTP client for the Fibery Entity Commands API.

    Usage:
        async with FiberyClient(api_key, workspace="harpin-ai") as client:
            user_map = await client.get_clockify_user_map()
    """

    def __init__(self, api_key: str, workspace: str = "harpin-ai") -> None:
        self._api_key = api_key
        self._workspace = workspace
        self._base_url = f"https://{workspace}.fibery.io"
        self._http: Optional[httpx.AsyncClient] = None
        # Fibery allows 3 requests/sec; Semaphore caps concurrency conservatively
        self._sem = asyncio.Semaphore(FIBERY_MAX_CONCURRENT)

    async def __aenter__(self) -> "FiberyClient":
        logger.info(
            f"FiberyClient opening session "
            f"(workspace={self._workspace}, key={_mask_key(self._api_key)})"
        )
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Token {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("FiberyClient session closed")

    # ── private helpers ───────────────────────────────────────────────────────

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Use FiberyClient as an async context manager")
        return self._http

    async def _post(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """POST a list of commands to /api/commands and return the results list."""
        async with self._sem:
            started = time.monotonic()
            logger.debug(
                f"→ POST {self._base_url}{FIBERY_COMMANDS_PATH} "
                f"({len(commands)} command(s))"
            )
            resp = await self._client.post(FIBERY_COMMANDS_PATH, json=commands)
            elapsed_ms = (time.monotonic() - started) * 1000
            body_preview = resp.text[:_MAX_BODY_LOG].replace("\n", " ")
            level = "DEBUG" if resp.status_code < 400 else "WARNING"
            logger.log(
                level,
                f"← {resp.status_code} ({elapsed_ms:.0f}ms, "
                f"{len(resp.content):,} bytes) | {body_preview}",
            )

        if resp.status_code == 401:
            raise AuthError("Invalid Fibery API token", status_code=401)
        if resp.status_code == 403:
            raise AuthError("Fibery access forbidden — check token permissions", status_code=403)
        if resp.status_code == 429:
            raise RateLimitError("Fibery rate limit exceeded", status_code=429)
        if resp.status_code not in (200, 204):
            raise ClockifyAPIError(
                f"Fibery API error {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )

        results: list[dict[str, Any]] = resp.json() if resp.status_code == 200 else []
        if not isinstance(results, list):
            results = [results]

        for i, r in enumerate(results):
            if isinstance(r, dict) and not r.get("success", True):
                logger.warning(
                    f"Fibery command[{i}] reported failure: "
                    f"{r.get('error', 'unknown error')}"
                )

        return results

    # ── public API ────────────────────────────────────────────────────────────

    async def verify_auth(self) -> bool:
        """Return True if the API token is accepted by the Fibery workspace."""
        try:
            await self._post([{
                "command": "fibery.entity/query",
                "args": {
                    "query": {
                        "q/from": "fibery/user",
                        "q/select": {"id": "fibery/id"},
                        "q/limit": 1,
                    }
                },
            }])
            logger.info("Fibery auth verified")
            return True
        except (AuthError, ClockifyAPIError) as exc:
            logger.warning(f"Fibery auth failed: {exc}")
            return False

    async def get_clockify_user_map(self) -> dict[str, str]:
        """Return {clockify_user_id: fibery_uuid} for all Clockify Users in Fibery."""
        results = await self._post([{
            "command": "fibery.entity/query",
            "args": {
                "query": {
                    "q/from": FIBERY_CLOCKIFY_USERS_TYPE,
                    "q/select": {
                        "id": "fibery/id",
                        "clockify_id": "Agreement Management/Clockify User ID",
                    },
                    "q/limit": "q/no-limit",
                }
            },
        }])
        rows: list[dict] = results[0].get("result", []) if results else []
        mapping: dict[str, str] = {}
        for row in rows:
            cid = row.get("clockify_id")
            fid = row.get("id")
            if cid and fid:
                mapping[cid] = fid
        logger.info(f"Loaded {len(mapping)} Clockify Users from Fibery")
        return mapping

    async def get_agreement_map(self) -> dict[str, str]:
        """Return {clockify_project_id: fibery_uuid} for all Agreements."""
        results = await self._post([{
            "command": "fibery.entity/query",
            "args": {
                "query": {
                    "q/from": FIBERY_AGREEMENTS_TYPE,
                    "q/select": {
                        "id": "fibery/id",
                        "project_id": "Agreement Management/Clockify Project ID",
                    },
                    "q/limit": "q/no-limit",
                }
            },
        }])
        rows = results[0].get("result", []) if results else []
        mapping: dict[str, str] = {}
        for row in rows:
            pid = row.get("project_id")
            fid = row.get("id")
            if pid and fid:
                mapping[pid] = fid
        logger.info(f"Loaded {len(mapping)} Agreements from Fibery")
        return mapping

    async def batch_upsert_labor_costs(self, entities: list[dict[str, Any]]) -> int:
        """Batch create-or-update Labor Cost entities using Time Log ID as conflict key.

        Generates a fresh fibery/id for each entity (required for creation).
        Returns the count of entities processed by the API.
        """
        if not entities:
            return 0

        # Assign a new UUID to each entity (Fibery uses it for creation; ignored on update)
        for e in entities:
            e.setdefault("fibery/id", str(uuid.uuid4()))

        results = await self._post([{
            "command": "fibery.entity.batch/create-or-update",
            "args": {
                "type": FIBERY_LABOR_COSTS_TYPE,
                "conflict-field": "Agreement Management/Time Log ID",
                "conflict-action": "update-latest",
                "entities": entities,
            },
        }])

        result = results[0] if results else {}
        if not result.get("success", False):
            raise ClockifyAPIError(
                f"Fibery batch upsert failed: {result.get('error', 'unknown')}"
            )

        # The result count may vary by Fibery version; fall back to input count
        count: int = len(result.get("result", entities))
        logger.debug(f"Batch upserted {count} Labor Cost entities")
        return count
