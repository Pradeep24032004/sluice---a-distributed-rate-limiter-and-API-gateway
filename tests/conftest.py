import uuid

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.asyncio.connection import BlockingConnectionPool

from app.db.session import dispose_engine
from app.http_client import close_http_client
from app.redis_client import close_redis


@pytest_asyncio.fixture
async def redis() -> Redis:
    # BlockingConnectionPool, matching production (app.redis_client.get_redis):
    # a bounded pool that makes callers queue for a free connection rather
    # than each of the concurrency-stress tests' thousands of asyncio.gather'd
    # requests opening its own new TCP connection, which can overwhelm the
    # local Redis server's single-threaded accept loop.
    pool = BlockingConnectionPool.from_url(
        "redis://localhost:6379/1", decode_responses=True, max_connections=50, timeout=20
    )
    client = Redis(connection_pool=pool)
    yield client
    await client.aclose()
    # aclose() only closes connections it currently has checked out;
    # BlockingConnectionPool's internal queue can still hold idle
    # connections from the concurrency-stress tests' bursts, which
    # would otherwise only get closed later via __del__ during
    # interpreter shutdown (after the event loop is gone) -- noisy but
    # harmless "Event loop is closed" warnings, not a real failure.
    await pool.disconnect()


@pytest.fixture
def client_key() -> str:
    """A unique key per test so tests never collide on shared Redis state."""
    return f"test:{uuid.uuid4()}"


@pytest_asyncio.fixture(autouse=True)
async def _reset_app_singletons():
    """app.redis_client and app.db.session hold process-wide singletons
    (the real production shape: one Redis client, one connection pool, all
    request lifetimes). pytest-asyncio gives each test function its own
    event loop, and asyncpg/Redis connections are bound to the loop that
    opened them -- so without this, a connection opened by test A breaks
    when test B's app-level code (get_redis/get_session) tries to reuse it
    in a different loop, raising "Event loop is closed". Tearing both down
    at the end of every test forces a fresh connection, in the right loop,
    next time."""
    yield
    await close_redis()
    await close_http_client()
    await dispose_engine()
