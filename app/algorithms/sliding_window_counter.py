import time

from app.algorithms.base import LuaRateLimiter, RateLimitResult


class SlidingWindowCounterLimiter(LuaRateLimiter):
    """Approximate sliding window: weighted average of the current and
    previous fixed-size window counters.

    Trade-off: O(1) memory per client (two counters), smooths out the
    fixed-window boundary-burst problem, but is an approximation rather
    than an exact count.
    """

    script_filename = "sliding_window_counter.lua"
    name = "sliding_window_counter"

    async def check(
        self, client_key: str, limit: int, window_seconds: int, **kwargs
    ) -> RateLimitResult:
        now_ms = int(time.time() * 1000)
        window_ms = window_seconds * 1000
        ttl = max(1, window_seconds * 2)

        key = f"rl:swc:{client_key}"
        allowed, remaining, retry_after_ms = await self._eval(
            [key], [now_ms, window_ms, limit, ttl]
        )
        return RateLimitResult(
            allowed=bool(allowed),
            remaining=int(remaining),
            retry_after_ms=int(retry_after_ms),
            algorithm=self.name,
        )
