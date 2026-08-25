import time

from redis.asyncio import Redis

from app.algorithms import RateLimitResult, get_limiter
from app.config import get_settings
from app.dependencies import ClientRateLimitConfig, resolve_config
from app.metrics import RATE_LIMIT_CHECK_LATENCY, RATE_LIMIT_DENIED


async def enforce_rate_limit(
    client_id: str, redis: Redis
) -> tuple[RateLimitResult, ClientRateLimitConfig]:
    """Resolve the client's config and run the matching algorithm. Does NOT
    raise on denial — callers check `result.allowed` themselves so the
    denied path still gets counted in metrics and the audit log."""
    settings = get_settings()
    config = await resolve_config(client_id, redis)
    limiter = get_limiter(config.algorithm, redis)

    start = time.perf_counter()
    result = await limiter.check(
        client_key=client_id,
        limit=config.limit,
        window_seconds=config.window_seconds,
        burst=config.burst,
    )
    RATE_LIMIT_CHECK_LATENCY.labels(
        algorithm=config.algorithm, instance_id=settings.instance_id
    ).observe(time.perf_counter() - start)

    if not result.allowed:
        RATE_LIMIT_DENIED.labels(
            algorithm=config.algorithm, instance_id=settings.instance_id
        ).inc()

    return result, config
