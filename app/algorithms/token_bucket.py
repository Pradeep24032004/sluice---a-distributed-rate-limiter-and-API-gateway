import time

from app.algorithms.base import LuaRateLimiter, RateLimitResult


class TokenBucketLimiter(LuaRateLimiter):
    """Allows bursts up to `capacity`, then refills at a steady rate.

    Trade-off: smooth long-run rate + burst tolerance, but a client that
    saves up tokens can fire `capacity` requests instantly.
    """

    script_filename = "token_bucket.lua"
    name = "token_bucket"

    async def check(
        self,
        client_key: str,
        limit: int,
        window_seconds: int,
        burst: int | None = None,
        **kwargs,
    ) -> RateLimitResult:
        capacity = burst or limit
        refill_rate = limit / window_seconds  # tokens per second
        now_ms = int(time.time() * 1000)
        ttl = max(1, window_seconds * 2)

        key = f"rl:tb:{client_key}"
        allowed, remaining, retry_after_ms = await self._eval(
            [key], [capacity, refill_rate, now_ms, 1, ttl]
        )
        return RateLimitResult(
            allowed=bool(allowed),
            remaining=int(remaining),
            retry_after_ms=int(retry_after_ms),
            algorithm=self.name,
        )
