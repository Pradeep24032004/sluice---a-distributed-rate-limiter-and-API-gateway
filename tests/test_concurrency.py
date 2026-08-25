"""Proves the atomic-Lua-script design actually does its job: under truly
concurrent load (many in-flight requests racing for the same client key,
via asyncio.gather rather than sequential awaits), exactly `limit` requests
succeed — never more.

This is the property a naive "GET count, check, INCR" implementation in
Python would violate: two coroutines could both read count=19 (limit=20)
before either writes back, and both would be allowed through, letting the
client burst past its limit. Redis executes each Lua script as a single
atomic unit, so the check-and-increment can't be interleaved even when the
Python client fires the requests concurrently.
"""

import asyncio

import pytest

from app.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from app.algorithms.sliding_window_log import SlidingWindowLogLimiter
from app.algorithms.token_bucket import TokenBucketLimiter

pytestmark = pytest.mark.asyncio

LIMIT = 20
CONCURRENCY = 200  # 10x the limit, all racing for the same key at once


async def _fire_concurrently(limiter, client_key, limit, window_seconds, **kwargs):
    results = await asyncio.gather(
        *[
            limiter.check(client_key, limit=limit, window_seconds=window_seconds, **kwargs)
            for _ in range(CONCURRENCY)
        ]
    )
    return sum(1 for r in results if r.allowed)


@pytest.mark.parametrize(
    "limiter_cls,kwargs",
    [
        (TokenBucketLimiter, {"burst": LIMIT}),
        (SlidingWindowLogLimiter, {}),
        (SlidingWindowCounterLimiter, {}),
    ],
    ids=["token_bucket", "sliding_window_log", "sliding_window_counter"],
)
async def test_concurrent_requests_never_exceed_limit(redis, client_key, limiter_cls, kwargs):
    limiter = limiter_cls(redis)

    allowed_count = await _fire_concurrently(
        limiter, client_key, limit=LIMIT, window_seconds=10, **kwargs
    )

    assert allowed_count == LIMIT


async def test_concurrent_requests_across_shared_limiter_instance(redis, client_key):
    """Same check, but reusing one limiter instance across all coroutines
    (as the real app does via app.algorithms.get_limiter) rather than a
    fresh instance per call — makes sure script-caching/evalsha reuse
    doesn't introduce its own race."""
    limiter = TokenBucketLimiter(redis)

    async def call():
        return await limiter.check(client_key, limit=LIMIT, window_seconds=10, burst=LIMIT)

    results = await asyncio.gather(*[call() for _ in range(CONCURRENCY)])
    allowed_count = sum(1 for r in results if r.allowed)

    assert allowed_count == LIMIT
