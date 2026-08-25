"""Scalability characterization for the rate-limit check path.

Not a correctness test (see tests/test_concurrency_stress.py for that) --
this measures raw throughput of the atomic Redis Lua check as concurrency
increases, using distinct keys per request so no single client's limit
throttles the numbers. The goal is to find the actual throughput curve and
where it saturates, rather than reporting one cherry-picked number.

Usage:
    python scripts/benchmark_scaling.py
"""

import asyncio
import time
import uuid

from redis.asyncio import Redis
from redis.asyncio.connection import BlockingConnectionPool

from app.algorithms.token_bucket import TokenBucketLimiter

REDIS_URL = "redis://localhost:6379/2"
POOL_MAX_CONNECTIONS = 50  # matches app.redis_client production default

CONCURRENCY_LEVELS = [100, 500, 1_000, 5_000, 10_000, 25_000, 50_000, 100_000]
SUSTAINED_WORKERS = 200
SUSTAINED_DURATION_SECONDS = 10


async def run_burst(redis: Redis, concurrency: int) -> tuple[float, float]:
    limiter = TokenBucketLimiter(redis)
    keys = [f"bench-{uuid.uuid4()}" for _ in range(concurrency)]

    start = time.perf_counter()
    await asyncio.gather(
        *[limiter.check(key, limit=1_000_000, window_seconds=100_000, burst=1_000_000) for key in keys]
    )
    elapsed = time.perf_counter() - start
    return elapsed, concurrency / elapsed


async def run_sustained(redis: Redis, workers: int, duration: float) -> tuple[int, float]:
    limiter = TokenBucketLimiter(redis)
    stop_at = time.perf_counter() + duration
    counts = [0] * workers

    async def worker(idx: int):
        key = f"bench-sustained-{uuid.uuid4()}"
        while time.perf_counter() < stop_at:
            await limiter.check(key, limit=1_000_000, window_seconds=100_000, burst=1_000_000)
            counts[idx] += 1

    start = time.perf_counter()
    await asyncio.gather(*[worker(i) for i in range(workers)])
    elapsed = time.perf_counter() - start
    total = sum(counts)
    return total, total / elapsed


async def main():
    pool = BlockingConnectionPool.from_url(
        REDIS_URL, decode_responses=True, max_connections=POOL_MAX_CONNECTIONS, timeout=60
    )
    redis = Redis(connection_pool=pool)
    await redis.flushdb()

    print(f"Bounded pool: max_connections={POOL_MAX_CONNECTIONS}\n")
    print(f"{'Concurrency':>12} | {'Wall clock':>10} | {'Throughput':>15}")
    print("-" * 45)

    results = []
    for concurrency in CONCURRENCY_LEVELS:
        elapsed, throughput = await run_burst(redis, concurrency)
        results.append((concurrency, elapsed, throughput))
        print(f"{concurrency:>12,} | {elapsed:>9.3f}s | {throughput:>12,.0f}/s")

    print(
        f"\nSustained load: {SUSTAINED_WORKERS} concurrent workers, "
        f"{SUSTAINED_DURATION_SECONDS}s continuous (not a single burst)"
    )
    total_ops, sustained_throughput = await run_sustained(
        redis, SUSTAINED_WORKERS, SUSTAINED_DURATION_SECONDS
    )
    print(f"Total operations: {total_ops:,}")
    print(f"Sustained throughput: {sustained_throughput:,.0f} checks/sec")

    await redis.aclose()
    await pool.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
