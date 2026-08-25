import json

from fastapi import Header, Request
from redis.asyncio import Redis

from app.config import get_settings
from app.db.crud import get_config
from app.db.session import session_scope
from app.redis_client import get_redis


def get_client_id(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    if x_api_key:
        return x_api_key
    if request.client:
        return request.client.host
    return "anonymous"


class ClientRateLimitConfig:
    def __init__(self, algorithm: str, limit: int, window_seconds: int, burst: int | None):
        self.algorithm = algorithm
        self.limit = limit
        self.window_seconds = window_seconds
        self.burst = burst


async def resolve_config(client_id: str, redis: Redis) -> ClientRateLimitConfig:
    """Look up a client's rate-limit config, cached in Redis so every
    request doesn't hit Postgres. Falls back to defaults from settings
    when the client has no row in `rate_limit_configs`."""
    settings = get_settings()
    cache_key = f"rl:config:{client_id}"

    cached = await redis.get(cache_key)
    if cached is not None:
        data = json.loads(cached)
        return ClientRateLimitConfig(**data)

    async with session_scope() as session:
        config = await get_config(session, client_id)

    if config is not None:
        result = ClientRateLimitConfig(
            algorithm=config.algorithm,
            limit=config.limit,
            window_seconds=config.window_seconds,
            burst=config.burst,
        )
    else:
        result = ClientRateLimitConfig(
            algorithm=settings.default_algorithm,
            limit=settings.default_limit,
            window_seconds=settings.default_window_seconds,
            burst=settings.default_burst,
        )

    await redis.set(
        cache_key,
        json.dumps(vars(result)),
        ex=settings.config_cache_ttl_seconds,
    )
    return result
