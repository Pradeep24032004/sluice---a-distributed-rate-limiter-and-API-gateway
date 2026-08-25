from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. FastAPI's own DI drives this generator to
    completion (running the `async with` cleanup below) after the request,
    even though route code just sees `Depends(get_session)` yield one
    session. For any call site outside FastAPI's DI, use `session_scope()`
    instead -- manually doing `async for s in get_session(): ...; break`
    abandons the generator without running that cleanup deterministically;
    it only happens later via GC, sometimes after the event loop is gone."""
    async with _session_factory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Use this outside of FastAPI route handlers (background tasks,
    scripts, tests) -- a real context manager, so cleanup is deterministic."""
    async with _session_factory() as session:
        yield session


async def dispose_engine() -> None:
    """Drop pooled connections. Used between tests: asyncpg connections are
    bound to the event loop that opened them, and each test in the suite
    runs in its own loop, so a pooled connection from a prior test would
    otherwise break the next one."""
    await _engine.dispose()
