import asyncio

import pytest

from app.algorithms.token_bucket import TokenBucketLimiter

pytestmark = pytest.mark.asyncio


async def test_allows_up_to_capacity(redis, client_key):
    limiter = TokenBucketLimiter(redis)
    for _ in range(5):
        result = await limiter.check(client_key, limit=5, window_seconds=5, burst=5)
        assert result.allowed

    result = await limiter.check(client_key, limit=5, window_seconds=5, burst=5)
    assert not result.allowed
    assert result.retry_after_ms > 0


async def test_refills_over_time(redis, client_key):
    limiter = TokenBucketLimiter(redis)
    # drain the bucket (capacity=2, refill=2 tokens/sec)
    for _ in range(2):
        assert (await limiter.check(client_key, limit=2, window_seconds=1, burst=2)).allowed
    assert not (await limiter.check(client_key, limit=2, window_seconds=1, burst=2)).allowed

    await asyncio.sleep(1.1)  # enough time for a full refill

    assert (await limiter.check(client_key, limit=2, window_seconds=1, burst=2)).allowed


async def test_burst_allows_more_than_steady_rate(redis, client_key):
    # limit=2/sec but burst capacity=10 -> first 10 requests succeed immediately
    limiter = TokenBucketLimiter(redis)
    results = [
        await limiter.check(client_key, limit=2, window_seconds=1, burst=10) for _ in range(10)
    ]
    assert all(r.allowed for r in results)
    assert not (await limiter.check(client_key, limit=2, window_seconds=1, burst=10)).allowed
