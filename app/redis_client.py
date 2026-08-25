from redis.asyncio import Redis
from redis.asyncio.connection import BlockingConnectionPool

from app.config import get_settings

_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        # BlockingConnectionPool, not the plain ConnectionPool: under a
        # burst of concurrent requests, callers queue (wait) for one of
        # `redis_max_connections` connections. The default ConnectionPool
        # instead raises immediately once max_connections is exceeded --
        # fine for a genuine config error, but wrong for "more concurrent
        # requests arrived than we have connections right now", which is
        # a completely normal and recoverable condition under load.
        pool = BlockingConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
            timeout=20,  # seconds to wait for a free connection before giving up
        )
        _redis = Redis(connection_pool=pool)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
