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


# ── get_clockify_user_map ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_clockify_user_map_returns_mapping():
    transport = _make_transport([
        (200, [{
            "success": True,
            "result": [
                {"id": "fibery-uuid-1", "clockify_id": "clockify-id-1"},
                {"id": "fibery-uuid-2", "clockify_id": "clockify-id-2"},
            ],
        }]),
    ])
    client = await _make_client(transport)
    mapping = await client.get_clockify_user_map()
    assert mapping == {
        "clockify-id-1": "fibery-uuid-1",
        "clockify-id-2": "fibery-uuid-2",
    }


@pytest.mark.asyncio
async def test_get_clockify_user_map_skips_rows_with_null_ids():
    transport = _make_transport([
        (200, [{
            "success": True,
            "result": [
                {"id": "fibery-uuid-1", "clockify_id": "clockify-id-1"},
                {"id": None, "clockify_id": "clockify-id-2"},   # bad row
                {"id": "fibery-uuid-3", "clockify_id": None},   # bad row
            ],
        }]),
    ])
    client = await _make_client(transport)
    mapping = await client.get_clockify_user_map()
    assert mapping == {"clockify-id-1": "fibery-uuid-1"}


# ── get_agreement_map ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_agreement_map_returns_mapping():
    transport = _make_transport([
        (200, [{
            "success": True,
            "result": [
                {"id": "ag-uuid-1", "project_id": "proj-1"},
                {"id": "ag-uuid-2", "project_id": None},  # no Clockify project → skip
            ],
        }]),
    ])
    client = await _make_client(transport)
    mapping = await client.get_agreement_map()
    assert mapping == {"proj-1": "ag-uuid-1"}


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
        clockify_user_fibery_id=None,
        agreement_fibery_id=None,
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
    # Fields must be present (even if null) so all entities share the same shape
    assert "Agreement Management/Task" in entity
    assert entity["Agreement Management/Task"] is None
    assert "Agreement Management/Task ID" in entity
    assert entity["Agreement Management/Task ID"] is None


def test_to_fibery_entity_includes_clockify_user_relation():
    entity = _make_payload(clockify_user_fibery_id="fibery-user-uuid").to_fibery_entity()
    assert entity["Agreement Management/Clockify User"] == {"fibery/id": "fibery-user-uuid"}


def test_to_fibery_entity_includes_agreement_relation():
    entity = _make_payload(agreement_fibery_id="fibery-ag-uuid").to_fibery_entity()
    assert entity["Agreement Management/Agreement"] == {"fibery/id": "fibery-ag-uuid"}


def test_to_fibery_entity_sets_relations_to_none_when_unmatched():
    """Unmatched relations must be present as null (not omitted) for batch shape uniformity."""
    entity = _make_payload(
        clockify_user_fibery_id=None, agreement_fibery_id=None
    ).to_fibery_entity()
    assert "Agreement Management/Clockify User" in entity
    assert entity["Agreement Management/Clockify User"] is None
    assert "Agreement Management/Agreement" in entity
    assert entity["Agreement Management/Agreement"] is None
