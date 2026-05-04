"""Async Fibery Entity Commands API client."""
import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import httpx
from loguru import logger

from clockify_cli.api.exceptions import AuthError, ClockifyAPIError, RateLimitError
from clockify_cli.constants import (
    FIBERY_BATCH_SIZE,
    FIBERY_CLOCKIFY_UPDATE_LOG_TYPE,
    FIBERY_COMMANDS_PATH,
    FIBERY_LABOR_COSTS_TYPE,
    FIBERY_MAX_CONCURRENT,
    FIBERY_TIME_ENTRY_STATUS_ENUM_TYPE,
)
from clockify_cli.fibery.models import ClockifyUpdateLogResult

_MAX_BODY_LOG = 300
_TIME_ENTRY_STATUS_FIELD = "Agreement Management/Time Entry Status"


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
        self._time_entry_status_id_by_name: dict[str, str] | None = None

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
                # Fibery puts error detail in result.message, not a top-level 'error' key
                result_body = r.get("result") or {}
                err_detail = (
                    result_body.get("message")
                    or result_body.get("name")
                    or r.get("error")
                    or "unknown error"
                )
                logger.warning(f"Fibery command[{i}] reported failure: {err_detail}")

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

    async def get_existing_time_log_ids(self) -> set[str]:
        """Return the set of Time Log IDs already present in Fibery Labor Costs."""
        results = await self._post([{
            "command": "fibery.entity/query",
            "args": {
                "query": {
                    "q/from": FIBERY_LABOR_COSTS_TYPE,
                    "q/select": {
                        "tlid": "Agreement Management/Time Log ID",
                    },
                    "q/limit": "q/no-limit",
                }
            },
        }])
        rows: list[dict] = results[0].get("result", []) if results else []
        existing = {r["tlid"] for r in rows if r.get("tlid")}
        logger.info(f"Pre-flight: {len(existing)} existing Labor Cost entries in Fibery")
        return existing

    async def get_last_clockify_update_run_at(self) -> Optional[str]:
        """Return the latest run timestamp from Fibery Clockify Update Log."""
        results = await self._post([{
            "command": "fibery.entity/query",
            "args": {
                "query": {
                    "q/from": FIBERY_CLOCKIFY_UPDATE_LOG_TYPE,
                    "q/select": {
                        "modified_at": "fibery/modification-date",
                    },
                    "q/limit": "q/no-limit",
                }
            },
        }])
        rows: list[dict[str, Any]] = results[0].get("result", []) if results else []
        modified_values = [row.get("modified_at") for row in rows if row.get("modified_at")]
        last_run = max(modified_values) if modified_values else None
        logger.info(
            "Clockify Update Log checkpoint: "
            f"{last_run if last_run else 'none (will run full push)'}"
        )
        return last_run

    async def append_clockify_update_log(self, result: ClockifyUpdateLogResult) -> None:
        """Append one summary row into Fibery Clockify Update Log."""
        response = await self._post([{
            "command": "fibery.entity/create",
            "args": {
                "type": FIBERY_CLOCKIFY_UPDATE_LOG_TYPE,
                "entity": {
                    "Agreement Management/Name": "Run completed",
                    "Agreement Management/Last Update": result.completed_at,
                    "Agreement Management/Records Updated": result.updated,
                    "Agreement Management/Records Inserted": result.created,
                    "Agreement Management/Records Skipped": result.skipped,
                },
            },
        }])
        if response and not response[0].get("success", False):
            result_body = response[0].get("result") or {}
            err_detail = (
                result_body.get("message")
                or result_body.get("name")
                or response[0].get("error")
                or "unknown"
            )
            raise ClockifyAPIError(f"Failed to write Clockify Update Log: {err_detail}")

    async def _get_time_entry_status_id_by_name(self) -> dict[str, str]:
        """Load enum IDs for Time Entry Status names."""
        if self._time_entry_status_id_by_name is not None:
            return self._time_entry_status_id_by_name

        results = await self._post([{
            "command": "fibery.entity/query",
            "args": {
                "query": {
                    "q/from": FIBERY_TIME_ENTRY_STATUS_ENUM_TYPE,
                    "q/select": {
                        "id": "fibery/id",
                        "name": "enum/name",
                    },
                    "q/limit": "q/no-limit",
                }
            },
        }])
        rows: list[dict[str, Any]] = results[0].get("result", []) if results else []
        self._time_entry_status_id_by_name = {
            str(row["name"]): str(row["id"])
            for row in rows
            if row.get("id") and row.get("name")
        }
        return self._time_entry_status_id_by_name

    async def _normalize_time_entry_status_field(
        self,
        entities: list[dict[str, Any]],
    ) -> None:
        """Convert text status values to enum entity references."""
        if not any(isinstance(e.get(_TIME_ENTRY_STATUS_FIELD), str) for e in entities):
            return

        id_by_name = await self._get_time_entry_status_id_by_name()
        for entity in entities:
            status_value = entity.get(_TIME_ENTRY_STATUS_FIELD)
            if not isinstance(status_value, str):
                continue
            enum_id = id_by_name.get(status_value)
            if enum_id:
                entity[_TIME_ENTRY_STATUS_FIELD] = {"fibery/id": enum_id}
            else:
                logger.warning(
                    f"Unknown time-entry status '{status_value}' for Fibery; "
                    "dropping field from payload."
                )
                entity.pop(_TIME_ENTRY_STATUS_FIELD, None)

    async def get_labor_cost_entity_ids(self) -> list[str]:
        """Return all Fibery UUIDs currently present in Labor Costs."""
        results = await self._post([{
            "command": "fibery.entity/query",
            "args": {
                "query": {
                    "q/from": FIBERY_LABOR_COSTS_TYPE,
                    "q/select": {
                        "id": "fibery/id",
                    },
                    "q/limit": "q/no-limit",
                }
            },
        }])
        rows: list[dict[str, Any]] = results[0].get("result", []) if results else []
        entity_ids = [row["id"] for row in rows if row.get("id")]
        logger.info(f"Found {len(entity_ids)} Labor Cost entities to delete")
        return entity_ids

    async def delete_labor_cost_entities(
        self,
        entity_ids: list[str],
        on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> int:
        """Delete Labor Cost entities by Fibery UUID."""
        if not entity_ids:
            return 0

        deleted = 0
        total = len(entity_ids)
        for batch_start in range(0, len(entity_ids), FIBERY_BATCH_SIZE):
            batch = entity_ids[batch_start : batch_start + FIBERY_BATCH_SIZE]
            commands = [
                {
                    "command": "fibery.entity/delete",
                    "args": {
                        "type": FIBERY_LABOR_COSTS_TYPE,
                        "entity": {"fibery/id": entity_id},
                    },
                }
                for entity_id in batch
            ]
            results = await self._post(commands)

            if not results:
                # Defensive fallback if API returns no command result objects.
                deleted += len(batch)
                continue

            for result in results:
                if not result.get("success", False):
                    result_body = result.get("result") or {}
                    err_detail = (
                        result_body.get("message")
                        or result_body.get("name")
                        or result.get("error")
                        or "unknown"
                    )
                    raise ClockifyAPIError(f"Fibery delete failed: {err_detail}")
                deleted += 1
            logger.debug(f"Deleted {deleted}/{total} Labor Cost entities")
            if on_progress:
                await on_progress(deleted, total)

        logger.info(f"Deleted {deleted} Labor Cost entities from Fibery")
        return deleted

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
        await self._normalize_time_entry_status_field(entities)

        async def _send_batch(batch_entities: list[dict[str, Any]]) -> dict[str, Any]:
            results = await self._post([{
                "command": "fibery.entity.batch/create-or-update",
                "args": {
                    "type": FIBERY_LABOR_COSTS_TYPE,
                    "conflict-field": "Agreement Management/Time Log ID",
                    "conflict-action": "update-latest",
                    "entities": batch_entities,
                },
            }])
            return results[0] if results else {}

        result = await _send_batch(entities)
        if not result.get("success", False):
            result_body = result.get("result") or {}
            err_detail = (
                result_body.get("message")
                or result_body.get("name")
                or result.get("error")
                or "unknown"
            )
            time_entry_status_field_problem = (
                _TIME_ENTRY_STATUS_FIELD in str(err_detail)
                and (
                    "schema-field-not-found" in str(result_body.get("name", ""))
                    or "parse-entity-field-failed" in str(result_body.get("name", ""))
                )
            )
            if time_entry_status_field_problem:
                logger.warning(
                    f"Fibery rejected field '{_TIME_ENTRY_STATUS_FIELD}'. "
                    "Retrying batch without it."
                )
                for entity in entities:
                    entity.pop(_TIME_ENTRY_STATUS_FIELD, None)
                result = await _send_batch(entities)
            if not result.get("success", False):
                result_body = result.get("result") or {}
                err_detail = (
                    result_body.get("message")
                    or result_body.get("name")
                    or result.get("error")
                    or "unknown"
                )
                raise ClockifyAPIError(f"Fibery batch upsert failed: {err_detail}")

        # The result count may vary by Fibery version; fall back to input count
        count: int = len(result.get("result", entities))
        logger.debug(f"Batch upserted {count} Labor Cost entities")
        return count
