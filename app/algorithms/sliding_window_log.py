import time

from app.algorithms.base import LuaRateLimiter, RateLimitResult


class SlidingWindowLogLimiter(LuaRateLimiter):
    """Exact sliding window: keeps a timestamp per request in a Redis ZSET.

    Trade-off: perfectly accurate (no boundary burst issue), but memory
    grows linearly with `limit` per client — expensive at high limits/scale.
    """

    script_filename = "sliding_window_log.lua"
    name = "sliding_window_log"

    async def check(
        self, client_key: str, limit: int, window_seconds: int, **kwargs
    ) -> RateLimitResult:
        now_ms = int(time.time() * 1000)
        window_ms = window_seconds * 1000
        ttl = max(1, window_seconds * 2)

        key = f"rl:swl:{client_key}"
        allowed, remaining, retry_after_ms = await self._eval(
            [key], [now_ms, window_ms, limit, ttl]
        )
        return RateLimitResult(
            allowed=bool(allowed),
            remaining=int(remaining),
            retry_after_ms=int(retry_after_ms),
            algorithm=self.name,
        )
