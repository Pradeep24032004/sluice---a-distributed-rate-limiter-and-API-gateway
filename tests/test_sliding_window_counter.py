import asyncio
import time

import pytest

from app.algorithms.sliding_window_counter import SlidingWindowCounterLimiter

pytestmark = pytest.mark.asyncio


async def test_allows_up_to_limit_within_a_window(redis, client_key):
    limiter = SlidingWindowCounterLimiter(redis)
    for _ in range(5):
        assert (await limiter.check(client_key, limit=5, window_seconds=5)).allowed

    result = await limiter.check(client_key, limit=5, window_seconds=5)
    assert not result.allowed


async def _sleep_until_window_start(window_seconds: int) -> None:
    """Block until just after a fixed-window boundary, so the next check's
    `elapsed_in_current` is near zero and test timing is deterministic
    rather than depending on where in the current second the test happened
    to start."""
    window_ms = window_seconds * 1000
    now_ms = time.time() * 1000
    wait_ms = window_ms - (now_ms % window_ms)
    await asyncio.sleep(wait_ms / 1000)


async def test_smooths_across_window_boundary(redis, client_key):
    """A fixed window resets hard at the boundary, letting 2x the limit
    through across two adjacent windows. The weighted counter should not
    allow a full extra `limit` immediately after the boundary, since the
    previous window's count still weighs heavily right after crossing."""
    window_seconds = 1
    limiter = SlidingWindowCounterLimiter(redis)

    await _sleep_until_window_start(window_seconds)

    # fill the window right after it starts, so elapsed_in_current ~ 0
    for _ in range(4):
        assert (await limiter.check(client_key, limit=4, window_seconds=window_seconds)).allowed
    assert not (await limiter.check(client_key, limit=4, window_seconds=window_seconds)).allowed

    await asyncio.sleep(window_seconds + 0.05)  # land just past the next boundary

    allowed_count = 0
    for _ in range(4):
        result = await limiter.check(client_key, limit=4, window_seconds=window_seconds)
        if result.allowed:
            allowed_count += 1
    assert allowed_count < 4
