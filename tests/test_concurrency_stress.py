"""Large-scale stress tests, separate from test_concurrency.py's baseline
proof. These answer questions a small test can't:

1. Does the limit still hold exactly under much heavier oversubscription
   (not just 10x, but up to 100x, at tens of thousands of concurrent
   requests)?
2. Do per-client keys stay correctly isolated when hundreds to a thousand
   different clients are all hammering the limiter at once, at scale?
   (A key-namespacing bug -- e.g. accidentally sharing a Lua key across
   clients -- would only show up under multi-key concurrent load at scale,
   never in a single-key or small-N test.)
3. How fast does the whole thing actually go, end to end, through the same
   bounded connection pool production uses?
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
        # window_seconds is huge for token bucket on purpose: token bucket
        # refills continuously (tokens/sec = limit/window_seconds), so a
        # short window would let a *correct* implementation admit slightly
        # more than `limit` simply because real wall-clock time passes
        # while thousands of requests are in flight. A long window keeps
        # the refill rate negligible for the test's duration, isolating
        # "does capacity hold under concurrency" from "does refill work"
        # (covered separately in test_token_bucket.py::test_refills_over_time).
        (TokenBucketLimiter, {"burst": 50}, 50, 2_500, 100_000),
        (SlidingWindowLogLimiter, {}, 50, 2_500, 10),
        (SlidingWindowCounterLimiter, {}, 50, 2_500, 10),
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


@pytest.mark.parametrize(
    "limiter_cls,kwargs,limit,concurrency,window_seconds",
    [
        # 100x oversubscription at 20,000 concurrent requests -- an order
        # of magnitude past the baseline stress test above, still on one
        # Redis key, still through the same 50-connection bounded pool.
        (TokenBucketLimiter, {"burst": 200}, 200, 20_000, 100_000),
        (SlidingWindowLogLimiter, {}, 200, 20_000, 10),
        (SlidingWindowCounterLimiter, {}, 200, 20_000, 10),
    ],
    ids=["token_bucket", "sliding_window_log", "sliding_window_counter"],
)
async def test_massive_oversubscription_still_enforces_exact_limit(
    redis, client_key, limiter_cls, kwargs, limit, concurrency, window_seconds
):
    """100x oversubscription: 20,000 concurrent requests racing for 200 slots."""
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


async def test_extreme_single_key_scale(redis, client_key):
    """The headline number: 50,000 truly concurrent requests (asyncio.gather,
    not a loop of awaits) racing for a single client's 500-request limit,
    all funneled through the same 50-connection bounded pool production
    uses. If the atomic Lua script or the pool were going to break under
    load, this is where it would show up."""
    limiter = TokenBucketLimiter(redis)
    limit = 500
    concurrency = 50_000

    start = time.perf_counter()
    results = await asyncio.gather(
        *[
            limiter.check(client_key, limit=limit, window_seconds=100_000, burst=limit)
            for _ in range(concurrency)
        ]
    )
    elapsed = time.perf_counter() - start

    allowed_count = sum(1 for r in results if r.allowed)
    assert allowed_count == limit

    print(
        f"\n[EXTREME] {concurrency} concurrent requests, limit={limit}: "
        f"{allowed_count} allowed, {elapsed:.3f}s wall clock "
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


async def test_massive_multi_client_isolation(redis):
    """The multi-client analogue of the extreme single-key test: 1000
    distinct clients (1000 distinct Redis keys), each firing 20 concurrent
    requests against their own limit of 10 -- 20,000 requests total, all
    in flight at once, all racing across 1000 different keys. A bug that
    let one client's counter leak into another's would show up here as
    some client seeing more or fewer than exactly 10 admitted."""
    limiter = TokenBucketLimiter(redis)
    num_clients = 1_000
    requests_per_client = 20
    limit = 10

    client_keys = [f"iso-test-massive-{uuid.uuid4()}" for _ in range(num_clients)]

    async def hit(key):
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
        f"\n[MASSIVE] {num_clients} clients x {requests_per_client} concurrent requests "
        f"({total} total): every client admitted exactly {limit}, {elapsed:.3f}s "
        f"wall clock ({total / elapsed:.0f} checks/sec)"
    )
