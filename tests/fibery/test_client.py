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


@pytest.mark.asyncio
async def test_get_labor_cost_entity_ids_returns_ids():
    transport = _make_transport([
        (200, [{
            "success": True,
            "result": [
                {"id": "11111111-1111-1111-1111-111111111111"},
                {"id": "22222222-2222-2222-2222-222222222222"},
            ],
        }]),
    ])
    client = await _make_client(transport)
    ids = await client.get_labor_cost_entity_ids()
    assert ids == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]


@pytest.mark.asyncio
async def test_get_last_clockify_update_run_at_returns_latest_modification_date():
    transport = _make_transport([
        (200, [{
            "success": True,
            "result": [
                {"modified_at": "2026-04-20T10:00:00.000Z"},
                {"modified_at": "2026-04-21T09:00:00.000Z"},
            ],
        }]),
    ])
    client = await _make_client(transport)
    latest = await client.get_last_clockify_update_run_at()
    assert latest == "2026-04-21T09:00:00.000Z"


@pytest.mark.asyncio
async def test_append_clockify_update_log_writes_entry():
    captured_payloads: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content.decode()))
        return httpx.Response(200, content=json.dumps([{"success": True}]).encode())

    client = FiberyClient("test-token-1234", workspace="test-ws")
    client._http = httpx.AsyncClient(
        base_url="https://test-ws.fibery.io",
        headers={"Authorization": "Token test-token-1234", "Content-Type": "application/json"},
        transport=httpx.MockTransport(handler),
    )
    from clockify_cli.fibery.models import ClockifyUpdateLogResult

    await client.append_clockify_update_log(
        ClockifyUpdateLogResult(
            workspace_id="ws-1",
            started_at="2026-04-21T10:00:00Z",
            completed_at="2026-04-21T10:01:00Z",
            status="done",
            total=10,
            pushed=10,
            created=2,
            updated=8,
            skipped=0,
            errors=0,
        )
    )
    assert captured_payloads
    assert captured_payloads[0][0]["command"] == "fibery.entity/create"
    assert captured_payloads[0][0]["args"]["type"] == "Agreement Management/Clockify Update Log"
    entity = captured_payloads[0][0]["args"]["entity"]
    assert entity["Agreement Management/Name"] == "Run completed"
    assert entity["Agreement Management/Last Update"] == "2026-04-21T10:01:00Z"
    assert entity["Agreement Management/Records Updated"] == 8
    assert entity["Agreement Management/Records Inserted"] == 2
    assert entity["Agreement Management/Records Skipped"] == 0


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


@pytest.mark.asyncio
async def test_batch_upsert_retries_without_time_entry_status_when_missing_in_fibery():
    captured_payloads: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        captured_payloads.append(payload)
        command = payload[0]["command"]
        if command == "fibery.entity/query":
            return httpx.Response(
                200,
                content=json.dumps([{
                    "success": True,
                    "result": [{"id": "enum-approved-id", "name": "APPROVED"}],
                }]).encode(),
            )
        if command == "fibery.entity.batch/create-or-update":
            entities = payload[0]["args"]["entities"]
            if "Agreement Management/Time Entry Status" in entities[0]:
                return httpx.Response(
                    200,
                    content=json.dumps([{
                        "success": False,
                        "result": {
                            "name": "entity.error/schema-field-not-found",
                            "message": (
                                "\"Agreement Management/Time Entry Status\" field was not found in "
                                "\"Agreement Management/Labor Costs\" database."
                            ),
                        },
                    }]).encode(),
                )
            return httpx.Response(
                200,
                content=json.dumps([{"success": True, "result": [{"fibery/id": "ok"}]}]).encode(),
            )
        raise AssertionError(f"Unexpected command: {command}")

    client = FiberyClient("test-token-1234", workspace="test-ws")
    client._http = httpx.AsyncClient(
        base_url="https://test-ws.fibery.io",
        headers={"Authorization": "Token test-token-1234", "Content-Type": "application/json"},
        transport=httpx.MockTransport(handler),
    )
    entities = [{
        "Agreement Management/Time Log ID": "te-1",
        "Agreement Management/Time Entry Status": "APPROVED",
    }]
    count = await client.batch_upsert_labor_costs(entities)
    assert count == 1
    assert len(captured_payloads) == 3  # enum query + failed upsert + retry upsert
    first_entities = captured_payloads[1][0]["args"]["entities"]
    second_entities = captured_payloads[2][0]["args"]["entities"]
    assert "Agreement Management/Time Entry Status" in first_entities[0]
    assert "Agreement Management/Time Entry Status" not in second_entities[0]


@pytest.mark.asyncio
async def test_batch_upsert_retries_without_time_entry_status_when_parse_fails():
    captured_payloads: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        captured_payloads.append(payload)
        command = payload[0]["command"]
        if command == "fibery.entity/query":
            return httpx.Response(
                200,
                content=json.dumps([{
                    "success": True,
                    "result": [{"id": "enum-pending-id", "name": "PENDING"}],
                }]).encode(),
            )
        if command == "fibery.entity.batch/create-or-update":
            entities = payload[0]["args"]["entities"]
            if "Agreement Management/Time Entry Status" in entities[0]:
                return httpx.Response(
                    200,
                    content=json.dumps([{
                        "success": False,
                        "result": {
                            "name": "entity.error/parse-entity-field-failed",
                            "message": (
                                "Cannot parse \"Agreement Management/Labor Costs\" entity "
                                "\"Agreement Management/Time Entry Status\" field."
                            ),
                        },
                    }]).encode(),
                )
            return httpx.Response(
                200,
                content=json.dumps([{"success": True, "result": [{"fibery/id": "ok"}]}]).encode(),
            )
        raise AssertionError(f"Unexpected command: {command}")

    client = FiberyClient("test-token-1234", workspace="test-ws")
    client._http = httpx.AsyncClient(
        base_url="https://test-ws.fibery.io",
        headers={"Authorization": "Token test-token-1234", "Content-Type": "application/json"},
        transport=httpx.MockTransport(handler),
    )
    entities = [{
        "Agreement Management/Time Log ID": "te-2",
        "Agreement Management/Time Entry Status": "PENDING",
    }]
    count = await client.batch_upsert_labor_costs(entities)
    assert count == 1
    assert len(captured_payloads) == 3  # enum query + failed upsert + retry upsert
    first_entities = captured_payloads[1][0]["args"]["entities"]
    second_entities = captured_payloads[2][0]["args"]["entities"]
    assert "Agreement Management/Time Entry Status" in first_entities[0]
    assert "Agreement Management/Time Entry Status" not in second_entities[0]


@pytest.mark.asyncio
async def test_batch_upsert_converts_time_entry_status_to_enum_reference():
    captured_payloads: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        captured_payloads.append(payload)
        command = payload[0]["command"]
        if command == "fibery.entity/query":
            return httpx.Response(
                200,
                content=json.dumps([{
                    "success": True,
                    "result": [{"id": "enum-approved-id", "name": "APPROVED"}],
                }]).encode(),
            )
        if command == "fibery.entity.batch/create-or-update":
            return httpx.Response(
                200,
                content=json.dumps([{"success": True, "result": [{"fibery/id": "ok"}]}]).encode(),
            )
        raise AssertionError(f"Unexpected command: {command}")

    client = FiberyClient("test-token-1234", workspace="test-ws")
    client._http = httpx.AsyncClient(
        base_url="https://test-ws.fibery.io",
        headers={"Authorization": "Token test-token-1234", "Content-Type": "application/json"},
        transport=httpx.MockTransport(handler),
    )
    entities = [{
        "Agreement Management/Time Log ID": "te-3",
        "Agreement Management/Time Entry Status": "APPROVED",
    }]
    count = await client.batch_upsert_labor_costs(entities)
    assert count == 1
    upsert_entities = captured_payloads[1][0]["args"]["entities"]
    assert upsert_entities[0]["Agreement Management/Time Entry Status"] == {
        "fibery/id": "enum-approved-id"
    }


@pytest.mark.asyncio
async def test_delete_labor_cost_entities_sends_delete_commands():
    captured_payloads: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content.decode()))
        return httpx.Response(200, content=json.dumps([{"success": True}, {"success": True}]).encode())

    client = FiberyClient("test-token-1234", workspace="test-ws")
    client._http = httpx.AsyncClient(
        base_url="https://test-ws.fibery.io",
        headers={"Authorization": "Token test-token-1234", "Content-Type": "application/json"},
        transport=httpx.MockTransport(handler),
    )

    count = await client.delete_labor_cost_entities([
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ])

    assert count == 2
    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    assert payload[0]["command"] == "fibery.entity/delete"
    assert payload[0]["args"]["entity"]["fibery/id"] == "11111111-1111-1111-1111-111111111111"
    assert payload[1]["args"]["entity"]["fibery/id"] == "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_delete_labor_cost_entities_reports_progress():
    transport = _make_transport([
        (200, [{"success": True}, {"success": True}]),
    ])
    client = await _make_client(transport)
    progress_calls: list[tuple[int, int]] = []

    async def on_progress(deleted: int, total: int) -> None:
        progress_calls.append((deleted, total))

    await client.delete_labor_cost_entities(
        [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ],
        on_progress=on_progress,
    )

    assert progress_calls == [(2, 2)]


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
        approval_status="NOT_SUBMITTED",
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
