"""Unit tests for FiberyClient using httpx.MockTransport."""
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from clockify_cli.api.exceptions import AuthError, RateLimitError
from clockify_cli.fibery.client import FiberyClient
from clockify_cli.fibery.models import LaborCostPayload


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_transport(responses: list[tuple[int, object]]) -> httpx.MockTransport:
    """Return a MockTransport that cycles through (status, body) pairs."""
    iterator = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = next(iterator)
        content = json.dumps(body).encode() if not isinstance(body, bytes) else body
        return httpx.Response(status, content=content)

    return httpx.MockTransport(handler)


async def _make_client(transport: httpx.MockTransport) -> FiberyClient:
    client = FiberyClient("test-token-1234", workspace="test-ws")
    client._http = httpx.AsyncClient(
        base_url="https://test-ws.fibery.io",
        headers={"Authorization": "Token test-token-1234", "Content-Type": "application/json"},
        transport=transport,
    )
    return client


# ── verify_auth ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_auth_returns_true_on_200():
    transport = _make_transport([
        (200, [{"success": True, "result": [{"id": "abc"}]}]),
    ])
    client = await _make_client(transport)
    assert await client.verify_auth() is True


@pytest.mark.asyncio
async def test_verify_auth_returns_false_on_401():
    transport = _make_transport([(401, {"error": "Unauthorized"})])
    client = await _make_client(transport)
    assert await client.verify_auth() is False


# ── get_existing_time_log_ids ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_existing_time_log_ids_returns_set():
    transport = _make_transport([
        (200, [{
            "success": True,
            "result": [
                {"tlid": "te-abc123"},
                {"tlid": "te-def456"},
            ],
        }]),
    ])
    client = await _make_client(transport)
    ids = await client.get_existing_time_log_ids()
    assert ids == {"te-abc123", "te-def456"}


@pytest.mark.asyncio
async def test_get_existing_time_log_ids_skips_null_entries():
    transport = _make_transport([
        (200, [{
            "success": True,
            "result": [
                {"tlid": "te-abc123"},
                {"tlid": None},      # manually created entry (no Time Log ID)
                {},                  # missing field entirely
            ],
        }]),
    ])
    client = await _make_client(transport)
    ids = await client.get_existing_time_log_ids()
    assert ids == {"te-abc123"}


@pytest.mark.asyncio
async def test_get_existing_time_log_ids_empty_db():
    transport = _make_transport([
        (200, [{"success": True, "result": []}]),
    ])
    client = await _make_client(transport)
    ids = await client.get_existing_time_log_ids()
    assert ids == set()


# ── batch_upsert_labor_costs ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_upsert_returns_count_on_success():
    entities = [{"Agreement Management/Time Log ID": f"te-{i}"} for i in range(3)]
    transport = _make_transport([
        (200, [{"success": True, "result": [{"fibery/id": "x"}, {"fibery/id": "y"}, {"fibery/id": "z"}]}]),
    ])
    client = await _make_client(transport)
    count = await client.batch_upsert_labor_costs(entities)
    assert count == 3


@pytest.mark.asyncio
async def test_batch_upsert_assigns_fibery_id_to_each_entity():
    """Each entity must have a fibery/id before the API call."""
    entities = [{"Agreement Management/Time Log ID": "te-1"}]
    transport = _make_transport([
        (200, [{"success": True, "result": [{"fibery/id": "new-uuid"}]}]),
    ])
    client = await _make_client(transport)
    await client.batch_upsert_labor_costs(entities)
    assert "fibery/id" in entities[0]
    assert len(entities[0]["fibery/id"]) == 36  # UUID format


@pytest.mark.asyncio
async def test_batch_upsert_returns_zero_for_empty_list():
    client = FiberyClient("key")
    client._http = MagicMock()  # should never be called
    count = await client.batch_upsert_labor_costs([])
    assert count == 0


@pytest.mark.asyncio
async def test_batch_upsert_raises_on_rate_limit():
    transport = _make_transport([(429, {"error": "rate limited"})])
    client = await _make_client(transport)
    with pytest.raises(RateLimitError):
        await client.batch_upsert_labor_costs([{"Agreement Management/Time Log ID": "x"}])


@pytest.mark.asyncio
async def test_batch_upsert_raises_on_auth_failure():
    transport = _make_transport([(401, {"error": "unauthorized"})])
    client = await _make_client(transport)
    with pytest.raises(AuthError):
        await client.batch_upsert_labor_costs([{"Agreement Management/Time Log ID": "x"}])


# ── LaborCostPayload.to_fibery_entity ─────────────────────────────────────────

def _make_payload(**kwargs) -> LaborCostPayload:
    defaults = dict(
        time_log_id="te-abc123",
        start_dt="2025-12-02T17:00:00Z",
        end_dt="2025-12-02T20:00:00Z",
        seconds=10800,
        hours=3.0,
        task="Code review",
        task_id=None,
        project_id="proj-1",
        billable="Yes",
        user_id_text="alice@example.com",
        user_name="Alice",
        project_name="Project Alpha",
    )
    defaults.update(kwargs)
    return LaborCostPayload(**defaults)


def test_to_fibery_entity_includes_required_fields():
    entity = _make_payload().to_fibery_entity()
    assert entity["Agreement Management/Time Log ID"] == "te-abc123"
    assert entity["Agreement Management/Clockify Hours"] == 3.0
    assert entity["Agreement Management/Seconds"] == 10800
    assert entity["Agreement Management/Billable"] == "Yes"


def test_to_fibery_entity_normalizes_datetime():
    entity = _make_payload(start_dt="2025-12-02T17:00:00Z").to_fibery_entity()
    assert entity["Agreement Management/Start Date Time"] == "2025-12-02T17:00:00.000Z"


def test_to_fibery_entity_already_has_millis_unchanged():
    entity = _make_payload(start_dt="2025-12-02T17:00:00.500Z").to_fibery_entity()
    assert entity["Agreement Management/Start Date Time"] == "2025-12-02T17:00:00.500Z"


def test_to_fibery_entity_includes_none_fields_as_null():
    """All fields must be present in every entity (Fibery batch shape requirement)."""
    entity = _make_payload(task=None, task_id=None).to_fibery_entity()
    assert "Agreement Management/Task" in entity
    assert entity["Agreement Management/Task"] is None
    assert "Agreement Management/Task ID" in entity
    assert entity["Agreement Management/Task ID"] is None


def test_to_fibery_entity_excludes_readonly_relation_fields():
    """Clockify User and Agreement are readonly in Fibery — must not be sent."""
    entity = _make_payload().to_fibery_entity()
    assert "Agreement Management/Clockify User" not in entity
    assert "Agreement Management/Agreement" not in entity
