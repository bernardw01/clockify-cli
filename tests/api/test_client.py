"""Tests for ClockifyClient using httpx mock transport."""
import json
import pytest
import httpx

from clockify_cli.api.client import ClockifyClient
from clockify_cli.api.exceptions import AuthError, NotFoundError, RateLimitError, ServerError
from clockify_cli.api.models import Workspace, Client, Project, WorkspaceUser, TimeEntry


# ── helpers ────────────────────────────────────────────────────────────────────

def make_response(body: object, status: int = 200, headers: dict | None = None) -> httpx.Response:
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    return httpx.Response(status, json=body, headers=h)


def mock_client(responses: list[httpx.Response]) -> ClockifyClient:
    """Build a ClockifyClient whose HTTP calls return pre-canned responses."""
    idx = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = idx["n"]
        idx["n"] += 1
        if i >= len(responses):
            raise AssertionError(f"Unexpected request #{i}: {request.url}")
        return responses[i]

    client = ClockifyClient("test-api-key")
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.clockify.me/api/v1",
        headers={"X-Api-Key": "test-api-key"},
    )
    return client


# ── workspaces ─────────────────────────────────────────────────────────────────

async def test_get_workspaces():
    payload = [{"id": "ws-1", "name": "Acme", "currencyCode": "USD", "imageUrl": None}]
    client = mock_client([make_response(payload)])
    workspaces = await client.get_workspaces()
    assert len(workspaces) == 1
    assert isinstance(workspaces[0], Workspace)
    assert workspaces[0].id == "ws-1"
    assert workspaces[0].currency_code == "USD"


async def test_get_workspaces_empty():
    client = mock_client([make_response([])])
    assert await client.get_workspaces() == []


# ── clients ────────────────────────────────────────────────────────────────────

async def test_get_clients_single_page():
    payload = [{"id": "c-1", "name": "Client A", "workspaceId": "ws-1", "archived": False}]
    client = mock_client([make_response(payload)])
    clients = await client.get_clients("ws-1")
    assert len(clients) == 1
    assert isinstance(clients[0], Client)
    assert clients[0].name == "Client A"


async def test_get_clients_pagination():
    page1 = [{"id": f"c-{i}", "name": f"C{i}", "workspaceId": "ws-1", "archived": False}
             for i in range(50)]
    page2 = [{"id": "c-50", "name": "C50", "workspaceId": "ws-1", "archived": False}]
    client = mock_client([make_response(page1), make_response(page2)])
    clients = await client.get_clients("ws-1")
    assert len(clients) == 51


# ── projects ────────────────────────────────────────────────────────────────────

async def test_get_projects():
    payload = [
        {"id": "p-1", "name": "Alpha", "workspaceId": "ws-1",
         "clientId": "c-1", "color": "#FF0000", "archived": False,
         "billable": True, "public": False},
    ]
    client = mock_client([make_response(payload)])
    projects = await client.get_projects("ws-1")
    assert len(projects) == 1
    assert isinstance(projects[0], Project)
    assert projects[0].client_id == "c-1"
    assert projects[0].billable is True


# ── users ──────────────────────────────────────────────────────────────────────

async def test_get_users():
    payload = [
        {"id": "u-1", "name": "Alice", "email": "alice@example.com",
         "status": "ACTIVE", "profilePicture": None},
    ]
    client = mock_client([make_response(payload)])
    users = await client.get_users("ws-1")
    assert len(users) == 1
    assert isinstance(users[0], WorkspaceUser)
    assert users[0].email == "alice@example.com"


# ── time entries ───────────────────────────────────────────────────────────────

ENTRY_PAYLOAD = {
    "id": "te-1",
    "workspaceId": "ws-1",
    "userId": "u-1",
    "projectId": "p-1",
    "taskId": None,
    "description": "Design work",
    "billable": True,
    "isLocked": False,
    "tagIds": [],
    "timeInterval": {
        "start": "2024-03-01T09:00:00Z",
        "end": "2024-03-01T10:30:00Z",
        "duration": "PT1H30M",
    },
}


async def test_iter_time_entries_single_page():
    client = mock_client([make_response([ENTRY_PAYLOAD], headers={"X-Total-Count": "1"})])
    pages = []
    async for entries, page, total in client.iter_time_entries("ws-1", "u-1"):
        pages.append((entries, page, total))

    assert len(pages) == 1
    entries, page, total = pages[0]
    assert page == 1
    assert len(entries) == 1
    assert isinstance(entries[0], TimeEntry)
    assert entries[0].description == "Design work"
    assert entries[0].time_interval.duration == "PT1H30M"


async def test_iter_time_entries_multiple_pages():
    page1 = [ENTRY_PAYLOAD] * 50
    page2 = [ENTRY_PAYLOAD]
    client = mock_client([
        make_response(page1, headers={"X-Total-Count": "51"}),
        make_response(page2, headers={"X-Total-Count": "51"}),
    ])
    all_entries: list[TimeEntry] = []
    async for entries, _page, _total in client.iter_time_entries("ws-1", "u-1"):
        all_entries.extend(entries)
    assert len(all_entries) == 51


async def test_iter_time_entries_empty():
    client = mock_client([make_response([], headers={"X-Total-Count": "0"})])
    pages = []
    async for batch in client.iter_time_entries("ws-1", "u-1"):
        pages.append(batch)
    assert pages == []


# ── error mapping ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,exc", [
    (401, AuthError),
    (403, AuthError),
    (404, NotFoundError),
    (429, RateLimitError),
    (500, ServerError),
])
async def test_error_mapping(status: int, exc: type):
    client = mock_client([make_response({"message": "error"}, status=status)])
    with pytest.raises(exc):
        await client.get_workspaces()


# ── models ─────────────────────────────────────────────────────────────────────

def test_workspace_to_db_dict():
    ws = Workspace(id="ws-1", name="Acme", currencyCode="USD")
    d = ws.to_db_dict()
    assert d["currency_code"] == "USD"
    assert d["id"] == "ws-1"


def test_time_entry_to_db_dict():
    entry = TimeEntry.model_validate(ENTRY_PAYLOAD)
    d = entry.to_db_dict()
    assert d["userId"] == "u-1"
    assert d["timeInterval"]["duration"] == "PT1H30M"
    assert d["billable"] is True


def test_time_entry_null_tag_ids_coerced_to_empty_list():
    """Regression: Clockify API returns tagIds: null on some entries."""
    payload = {**ENTRY_PAYLOAD, "tagIds": None}
    entry = TimeEntry.model_validate(payload)
    assert entry.tag_ids == []


def test_time_entry_missing_tag_ids_defaults_to_empty_list():
    payload = {k: v for k, v in ENTRY_PAYLOAD.items() if k != "tagIds"}
    entry = TimeEntry.model_validate(payload)
    assert entry.tag_ids == []
