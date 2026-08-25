import asyncio

import pytest

from app.algorithms.sliding_window_log import SlidingWindowLogLimiter

pytestmark = pytest.mark.asyncio


async def test_allows_up_to_limit_exactly(redis, client_key):
    limiter = SlidingWindowLogLimiter(redis)
    for _ in range(5):
        assert (await limiter.check(client_key, limit=5, window_seconds=5)).allowed

    result = await limiter.check(client_key, limit=5, window_seconds=5)
    assert not result.allowed


async def test_window_slides_smoothly_not_in_fixed_buckets(redis, client_key):
    """Unlike a fixed window, requests only free up once *they* age out —
    not at a shared window boundary."""
    limiter = SlidingWindowLogLimiter(redis)

    assert (await limiter.check(client_key, limit=1, window_seconds=1)).allowed
    assert not (await limiter.check(client_key, limit=1, window_seconds=1)).allowed

    await asyncio.sleep(1.1)

    assert (await limiter.check(client_key, limit=1, window_seconds=1)).allowed


async def test_remaining_count_decreases(redis, client_key):
    limiter = SlidingWindowLogLimiter(redis)
    first = await limiter.check(client_key, limit=3, window_seconds=5)
    second = await limiter.check(client_key, limit=3, window_seconds=5)
    assert first.remaining == 2
    assert second.remaining == 1
