from redis.asyncio import Redis

from app.algorithms.base import LuaRateLimiter, RateLimitResult
from app.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from app.algorithms.sliding_window_log import SlidingWindowLogLimiter
from app.algorithms.token_bucket import TokenBucketLimiter

ALGORITHMS: dict[str, type[LuaRateLimiter]] = {
    TokenBucketLimiter.name: TokenBucketLimiter,
    SlidingWindowLogLimiter.name: SlidingWindowLogLimiter,
    SlidingWindowCounterLimiter.name: SlidingWindowCounterLimiter,
}

_instances: dict[tuple[str, int], LuaRateLimiter] = {}


def get_limiter(algorithm: str, redis: Redis) -> LuaRateLimiter:
    """Cached per (algorithm, redis client identity) rather than just
    algorithm. A limiter instance captures the Lua script's cached SHA
    against one specific Redis connection; keying on algorithm alone would
    permanently pin it to whichever client happened to be passed on the
    first call, silently going stale if the app ever gets a new client
    (e.g. reconnecting after Redis restarts)."""
    if algorithm not in ALGORITHMS:
        raise ValueError(f"Unknown rate-limit algorithm: {algorithm}")
    key = (algorithm, id(redis))
    if key not in _instances:
        _instances[key] = ALGORITHMS[algorithm](redis)
    return _instances[key]


__all__ = ["ALGORITHMS", "get_limiter", "RateLimitResult", "LuaRateLimiter"]
