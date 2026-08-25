"""End-to-end tests against the real ASGI app (app.main.app) — not through
nginx/docker, but exercising the actual FastAPI routes, real Redis, real
Postgres, and a real upstream call, wired together exactly as production
would run them. Complements the algorithm-level unit tests by proving the
pieces integrate correctly (admin config -> cache invalidation -> gateway
enforcement -> audit log)."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import app

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def api_key():
    return f"itest-{uuid.uuid4()}"


async def test_proxy_forwards_to_upstream_with_default_limit(client, api_key):
    settings = get_settings()
    response = await client.get("/proxy/orders/42", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    assert response.json()["path"] == "orders/42"
    assert response.headers["X-RateLimit-Limit"] == str(settings.default_limit)
    assert response.headers["X-RateLimit-Remaining"] == str(settings.default_limit - 1)


async def test_proxy_returns_429_with_retry_after_once_over_limit(client, api_key):
    put_resp = await client.put(
        f"/admin/limits/{api_key}",
        json={"algorithm": "token_bucket", "limit": 3, "window_seconds": 5, "burst": 3},
    )
    assert put_resp.status_code == 200

    statuses = []
    for _ in range(4):
        r = await client.get("/proxy/orders/1", headers={"X-API-Key": api_key})
        statuses.append(r.status_code)

    assert statuses == [200, 200, 200, 429]

    denied = await client.get("/proxy/orders/1", headers={"X-API-Key": api_key})
    assert denied.status_code == 429
    assert "Retry-After" in denied.headers
    assert denied.headers["X-RateLimit-Remaining"] == "0"
    assert denied.headers["X-RateLimit-Algorithm"] == "token_bucket"


async def test_admin_config_roundtrip_and_cache_invalidation(client, api_key):
    await client.put(
        f"/admin/limits/{api_key}",
        json={"algorithm": "sliding_window_log", "limit": 5, "window_seconds": 5},
    )
    got = await client.get(f"/admin/limits/{api_key}")
    assert got.status_code == 200
    assert got.json() == {
        "client_id": api_key,
        "algorithm": "sliding_window_log",
        "limit": 5,
        "window_seconds": 5,
        "burst": None,
    }

    # PUT again with a different algorithm; the gateway must pick up the
    # change immediately rather than serving a 5s-stale cached config
    await client.put(
        f"/admin/limits/{api_key}",
        json={"algorithm": "token_bucket", "limit": 2, "window_seconds": 5, "burst": 2},
    )
    got2 = await client.get(f"/admin/limits/{api_key}")
    assert got2.json()["algorithm"] == "token_bucket"
    assert got2.json()["limit"] == 2

    delete_resp = await client.delete(f"/admin/limits/{api_key}")
    assert delete_resp.status_code == 200

    # deleting again is a 404 -- there's nothing left to delete
    assert (await client.delete(f"/admin/limits/{api_key}")).status_code == 404

    # back to defaults now that the override is gone
    settings = get_settings()
    back_to_default = await client.get(f"/admin/limits/{api_key}")
    assert back_to_default.json()["algorithm"] == settings.default_algorithm


async def test_unknown_algorithm_is_rejected(client, api_key):
    response = await client.put(
        f"/admin/limits/{api_key}",
        json={"algorithm": "quantum_bucket", "limit": 5, "window_seconds": 5},
    )
    assert response.status_code == 400


async def test_denied_requests_are_audited_and_visible_in_violations(client, api_key):
    await client.put(
        f"/admin/limits/{api_key}",
        json={"algorithm": "token_bucket", "limit": 1, "window_seconds": 5, "burst": 1},
    )
    await client.get("/proxy/orders/1", headers={"X-API-Key": api_key})  # consumes the 1 slot
    await client.get("/proxy/orders/1", headers={"X-API-Key": api_key})  # denied

    violations = await client.get("/admin/violations", params={"limit": 500})
    assert violations.status_code == 200
    assert any(v["client_id"] == api_key for v in violations.json())


async def test_health_and_algorithms_endpoints(client):
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["redis"] is True

    algorithms = await client.get("/admin/algorithms")
    assert set(algorithms.json()) == {
        "token_bucket",
        "sliding_window_log",
        "sliding_window_counter",
    }
