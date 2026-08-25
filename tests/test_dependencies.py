"""Tests for the config-resolution + Redis caching layer that sits between
every request and Postgres. This is the piece that keeps the hot path fast
(don't hit Postgres per-request) — worth proving it actually caches, and
that a config change is picked up promptly rather than silently ignored."""

import uuid

import pytest

from app.config import get_settings
from app.db.crud import upsert_config
from app.db.session import session_scope
from app.dependencies import resolve_config
from app.redis_client import get_redis

pytestmark = pytest.mark.asyncio


@pytest.fixture
def client_id():
    return f"dep-test-{uuid.uuid4()}"


async def test_resolve_config_falls_back_to_defaults(client_id):
    redis = await get_redis()
    settings = get_settings()

    config = await resolve_config(client_id, redis)

    assert config.algorithm == settings.default_algorithm
    assert config.limit == settings.default_limit
    assert config.window_seconds == settings.default_window_seconds


async def test_resolve_config_picks_up_db_override(client_id):
    redis = await get_redis()
    await redis.delete(f"rl:config:{client_id}")

    async with session_scope() as session:
        await upsert_config(session, client_id, "sliding_window_counter", 7, 30, None)

    config = await resolve_config(client_id, redis)

    assert config.algorithm == "sliding_window_counter"
    assert config.limit == 7
    assert config.window_seconds == 30


async def test_resolve_config_is_cached_and_survives_a_db_change(client_id):
    """Once cached, a direct DB write shouldn't be visible until the cache
    entry expires -- proves we're actually reading from Redis, not hitting
    Postgres on every call."""
    redis = await get_redis()
    await redis.delete(f"rl:config:{client_id}")

    async with session_scope() as session:
        await upsert_config(session, client_id, "token_bucket", 10, 10, 10)

    first = await resolve_config(client_id, redis)
    assert first.limit == 10

    # bypass the cache-invalidating admin endpoint and write straight to
    # the DB, simulating an out-of-band change
    async with session_scope() as session:
        await upsert_config(session, client_id, "token_bucket", 999, 10, 999)

    second = await resolve_config(client_id, redis)
    assert second.limit == 10  # still the cached value, not 999
