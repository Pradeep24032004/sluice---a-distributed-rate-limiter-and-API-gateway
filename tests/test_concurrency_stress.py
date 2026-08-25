"""Larger-scale stress tests, separate from test_concurrency.py's baseline
proof. These answer two different questions:

1. Does the limit still hold exactly under much heavier oversubscription
   (not just 10x, but 50x)?
2. Do per-client keys stay correctly isolated when many different clients
   are all hammering the limiter at once? (A key-namespacing bug —
   e.g. accidentally sharing a Lua key across clients — would only show up
   under multi-key concurrent load, never in a single-key test.)
"""

import asyncio
import time
import uuid

import pytest

from app.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from app.algorithms.sliding_window_log import SlidingWindowLogLimiter
from app.algorithms.token_bucket import TokenBucketLimiter

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "limiter_cls,kwargs,limit,concurrency,window_seconds",
    [
        # window_seconds is huge here on purpose: token bucket refills
        # continuously (tokens/sec = limit/window_seconds), so a short
        # window would let a *correct* implementation admit slightly more
        # than `limit` simply because real wall-clock time passes while
        # 2500 requests are in flight. A long window keeps the refill rate
        # negligible for the test's duration, isolating "does capacity hold
        # under concurrency" from "does refill work" (covered separately
        # in test_token_bucket.py::test_refills_over_time).
        (TokenBucketLimiter, {"burst": 50}, 50, 2500, 100_000),
        (SlidingWindowLogLimiter, {}, 50, 2500, 10),
        (SlidingWindowCounterLimiter, {}, 50, 2500, 10),
    ],
    ids=["token_bucket", "sliding_window_log", "sliding_window_counter"],
)
async def test_heavy_oversubscription_still_enforces_exact_limit(
    redis, client_key, limiter_cls, kwargs, limit, concurrency, window_seconds
):
    """50x oversubscription on a single key: 2500 requests racing for 50 slots."""
    limiter = limiter_cls(redis)

    start = time.perf_counter()
    results = await asyncio.gather(
        *[
            limiter.check(client_key, limit=limit, window_seconds=window_seconds, **kwargs)
            for _ in range(concurrency)
        ]
    )
    elapsed = time.perf_counter() - start

    allowed_count = sum(1 for r in results if r.allowed)
    assert allowed_count == limit

    print(
        f"\n[{limiter_cls.__name__}] {concurrency} concurrent requests, "
        f"limit={limit}: {allowed_count} allowed, {elapsed:.3f}s wall clock "
        f"({concurrency / elapsed:.0f} checks/sec)"
    )


async def test_many_clients_stay_isolated_under_simultaneous_load(redis):
    """100 distinct clients, each with limit=10, all firing 20 requests
    (2x their own limit) at the exact same time -- 2000 requests total,
    racing across 100 different Redis keys simultaneously. Every client
    must get exactly 10 allowed, regardless of what every other client is
    doing concurrently."""
    limiter = TokenBucketLimiter(redis)
    num_clients = 100
    requests_per_client = 20
    limit = 10

    client_keys = [f"iso-test-{uuid.uuid4()}" for _ in range(num_clients)]

    async def hit(key):
        # large window_seconds keeps refill negligible during the test, so
        # this isolates key-isolation from token-bucket refill timing
        return key, await limiter.check(key, limit=limit, window_seconds=100_000, burst=limit)

    start = time.perf_counter()
    results = await asyncio.gather(
        *[hit(key) for key in client_keys for _ in range(requests_per_client)]
    )
    elapsed = time.perf_counter() - start

    allowed_per_client = {}
    for key, result in results:
        allowed_per_client.setdefault(key, 0)
        if result.allowed:
            allowed_per_client[key] += 1

    assert len(allowed_per_client) == num_clients
    assert all(count == limit for count in allowed_per_client.values())

    total = num_clients * requests_per_client
    print(
        f"\n{num_clients} clients x {requests_per_client} concurrent requests "
        f"({total} total): every client admitted exactly {limit}, {elapsed:.3f}s "
        f"wall clock ({total / elapsed:.0f} checks/sec)"
    )
