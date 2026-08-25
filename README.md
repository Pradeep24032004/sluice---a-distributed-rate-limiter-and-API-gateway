# Sluice

[![CI](https://github.com/Pradeep24032004/sluice---a-distributed-rate-limiter-and-API-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/Pradeep24032004/sluice---a-distributed-rate-limiter-and-API-gateway/actions/workflows/ci.yml)

A distributed rate limiter and API gateway in FastAPI, with rate-limit
state shared across multiple gateway instances via Redis — so scaling the
gateway horizontally doesn't let clients bypass their limit by hitting a
different instance. Implements Token Bucket, Sliding Window Log, and
Sliding Window Counter algorithms as atomic Redis Lua scripts. Stress-tested
up to 50,000 concurrent requests and 1,000 simultaneous clients with the
limit holding exactly every time, and characterized to a sustained ~32,000
checks/sec ceiling set by the connection pool (see [Tests](#tests)).

## Architecture

```
Locust  ->  nginx (LB)  ->  gateway-1 \
                        ->  gateway-2  -> Redis (shared limiter state)
                        ->  gateway-3 /        -> Postgres (config + audit log)
                                     \-> upstream (mock backend)

Prometheus scrapes all 3 gateway instances -> Grafana dashboard
```

- **Gateway** (`app/`): FastAPI service. `/proxy/{path}` enforces the
  caller's rate limit, then forwards to the upstream service.
- **Rate-limit algorithms** (`app/algorithms/`): three strategies, each
  implemented as an atomic Redis Lua script (`app/scripts/*.lua`) so the
  check-and-increment is race-free across concurrent gateway instances.
- **Config + audit** (`app/db/`): PostgreSQL stores per-client overrides
  (`rate_limit_configs`) and a request audit log (`request_logs`), cached
  in Redis for 5s so the hot path doesn't hit Postgres per-request.
- **Multi-instance simulation**: `docker-compose.yml` runs 3 gateway
  replicas behind nginx (least-conn LB), proving limits are enforced
  globally, not per-instance.
- **Observability**: Prometheus scrapes gateway metrics; Grafana
  visualizes request rate, denial rate, and latency percentiles.

## Rate-limiting algorithms and trade-offs

| Algorithm | Memory | Accuracy | Notes |
|---|---|---|---|
| **Token Bucket** | O(1) per client | Approximate but smooth | Allows configurable bursts up to `capacity`, then a steady refill rate. Best default for most APIs. |
| **Sliding Window Log** | O(limit) per client | Exact | Stores every request timestamp in a Redis ZSET. No boundary-burst issue, but memory scales with the limit — expensive for high-limit clients. |
| **Sliding Window Counter** | O(1) per client | Approximate | Weighted average of the current and previous fixed-window counters. Fixes the fixed-window boundary-burst problem cheaply, at the cost of being an estimate rather than an exact count. |

All three are implemented and selectable per-client via the admin API —
see `app/scripts/*.lua` for the atomic logic.

## Running it

```bash
cp .env.example .env
docker compose up -d --build
```

- Gateway (via LB): http://localhost:8080
- Direct Prometheus metrics: http://localhost:8080/metrics
- Prometheus UI: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous viewer access enabled)

Try it:

```bash
curl http://localhost:8080/proxy/orders/123 -H "X-API-Key: demo"
# repeat quickly past the default limit (20 req / 10s) to see a 429
```

Set a custom per-client limit:

```bash
curl -X PUT http://localhost:8080/admin/limits/demo \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "sliding_window_counter", "limit": 5, "window_seconds": 5}'
```

View denied requests:

```bash
curl http://localhost:8080/admin/violations
```

## Tests

```bash
pip install -r requirements-dev.txt
docker compose up -d redis postgres upstream
alembic upgrade head
pytest -v
```

30 tests, run against real Redis and Postgres and the actual FastAPI app
(no mocks). A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the
suite against Postgres/Redis service containers on every push.

| File | Count | What it proves |
|---|---|---|
| `test_token_bucket.py` | 3 | Capacity enforcement, refill-over-time, burst-vs-steady-rate behavior |
| `test_sliding_window_log.py` | 3 | Exact limit enforcement, window slides per-request rather than in fixed buckets, remaining-count accounting |
| `test_sliding_window_counter.py` | 2 | Limit enforcement, boundary-smoothing (deterministically aligned to a window boundary — naive wall-clock timing here is flaky, see the test's comment for why) |
| `test_concurrency.py` | 4 | **Core correctness claim**: fires 200 truly concurrent requests (`asyncio.gather`, not sequential awaits) at a single client key with `limit=20`, across all 3 algorithms — asserts exactly 20 succeed |
| `test_concurrency_stress.py` | 9 | Large-scale: 50x and 100x oversubscription (up to 20,000 concurrent requests for 200 slots), an extreme single-key run at **50,000 concurrent requests**, and multi-client isolation at up to **1,000 simultaneous clients / 20,000 concurrent requests** — every client gets exactly its own limit, no cross-client leakage |
| `test_gateway_integration.py` | 6 | End-to-end through the real ASGI app: proxy → upstream forwarding, 429 + `Retry-After` once over limit, admin config CRUD with immediate cache invalidation, rejected unknown algorithms, denied requests appearing in the audit log |
| `test_dependencies.py` | 3 | Config resolution falls back to defaults, picks up a Postgres override, and is correctly served from the Redis cache rather than hitting Postgres every request |

### Benchmark results

Full suite:

```
30 passed in 13.75s
```

Concurrency and stress suite (`pytest -s tests/test_concurrency_stress.py`),
each case firing requests via `asyncio.gather` for true concurrency rather
than sequential awaits:

```
[TokenBucketLimiter] 2500 concurrent requests, limit=50: 50 allowed, 0.156s wall clock (15997 checks/sec)
[SlidingWindowLogLimiter] 2500 concurrent requests, limit=50: 50 allowed, 0.155s wall clock (16087 checks/sec)
[SlidingWindowCounterLimiter] 2500 concurrent requests, limit=50: 50 allowed, 0.158s wall clock (15833 checks/sec)
[TokenBucketLimiter] 20000 concurrent requests, limit=200: 200 allowed, 1.403s wall clock (14253 checks/sec)
[SlidingWindowLogLimiter] 20000 concurrent requests, limit=200: 200 allowed, 1.355s wall clock (14760 checks/sec)
[SlidingWindowCounterLimiter] 20000 concurrent requests, limit=200: 200 allowed, 1.355s wall clock (14758 checks/sec)
[EXTREME] 50000 concurrent requests, limit=500: 500 allowed, 3.387s wall clock (14761 checks/sec)
100 clients x 20 concurrent requests (2000 total): every client admitted exactly 10, 0.131s wall clock (15295 checks/sec)
[MASSIVE] 1000 clients x 20 concurrent requests (20000 total): every client admitted exactly 10, 1.375s wall clock (14547 checks/sec)
```

The limit held exactly at every scale tested, from 2,500 up through 50,000
concurrent requests on a single key, and every one of the 1,000 simultaneous
clients in the isolation test was admitted exactly its own configured limit.
Repeated across multiple runs with no variance.

A naive `GET count; if count < limit: INCR` implementation in application
code would race under concurrency — two coroutines can both read `count=19`
before either writes back, letting both through and breaking the limit.
These tests fire requests concurrently specifically to trigger that failure
mode, and confirm Redis's atomic Lua script execution prevents it at every
concurrency level tested.

### Design notes

Two issues surfaced during stress testing and were fixed:

1. **Limiter cache keyed by algorithm only.** `get_limiter()` originally
   cached each algorithm's Lua-script wrapper keyed on algorithm name alone,
   permanently binding it to whichever Redis client instance was passed on
   the first call. A reconnect (a new client instance) would leave cached
   limiters pointing at a dead connection. Fixed by keying the cache on
   `(algorithm, id(redis))` — see `app/algorithms/__init__.py`.
2. **Unbounded Redis connection pool.** The Redis client had no
   `max_connections` cap, so a burst of concurrent requests opened one new
   TCP connection per in-flight request rather than reusing a bounded pool —
   thousands of connections under the stress tests' concurrency, which
   degraded throughput measurably. Fixed with a `BlockingConnectionPool`
   (`max_connections=50`, callers queue for a free connection instead of
   erroring). Effect on the 2,500-concurrent-request case: ~5,500 checks/sec
   unbounded vs. ~16,000 checks/sec bounded — reusing warm connections
   outperforms a fresh TCP handshake per request.

### Scalability characterization

`scripts/benchmark_scaling.py` measures raw throughput of the rate-limit
check path (distinct keys per request, so no client's own limit throttles
the result) across a range of concurrency levels, plus a sustained-load
run to distinguish burst performance from steady-state performance:

```bash
PYTHONPATH=. python scripts/benchmark_scaling.py
```

```
Bounded pool: max_connections=50

 Concurrency | Wall clock |      Throughput
---------------------------------------------
         100 |     0.011s |        9,032/s
         500 |     0.025s |       20,079/s
       1,000 |     0.052s |       19,257/s
       5,000 |     0.289s |       17,289/s
      10,000 |     0.579s |       17,258/s
      25,000 |     1.686s |       14,830/s
      50,000 |     3.578s |       13,974/s
     100,000 |     7.350s |       13,606/s

Sustained load: 200 concurrent workers, 10s continuous (not a single burst)
Total operations: 312,228
Sustained throughput: 31,197 checks/sec
```

Single-burst throughput peaks around 500-5,000 concurrent requests
(~18,000-20,000/s) and declines as `asyncio.gather` is asked to schedule
tens of thousands of coroutines at once — that decline is Python-side
scheduling overhead, not Redis. A sustained worker-pool pattern (a fixed
number of workers issuing requests continuously for 10 seconds, rather
than one giant burst) avoids that overhead and sustains higher throughput:

```
workers=  50  total_ops=272,740  throughput=    34,090/s
workers= 100  total_ops=258,246  throughput=    32,269/s
workers= 200  total_ops=259,251  throughput=    32,373/s
workers= 400  total_ops=262,034  throughput=    32,678/s
workers= 800  total_ops=249,577  throughput=    31,046/s
```

Sustained throughput plateaus at 31,000-34,000 checks/sec regardless of
worker count from 50 to 800, which identifies the actual limiting resource:
the 50-connection bounded pool, not application-level concurrency. Raising
`redis_max_connections` (`app/config.py`) would be the next lever to pull
for higher sustained throughput.

## Load testing

Locust drives the gateway through the nginx LB with two profiles —
`SteadyUser` (under the limit) and `BurstyUser` (over the limit, to
exercise the 429 path):

```bash
pip install -r requirements-dev.txt
locust -f load_test/locustfile.py --host http://localhost:8080
# or headless, e.g.:
locust -f load_test/locustfile.py SteadyUser --host http://localhost:8080 --headless -u 1000 -r 100 --run-time 45s
```

Numbers below are from the full Docker Compose stack (3 gateway replicas +
nginx + Redis + Postgres + Locust) on a single M-series laptop, with the
load generator sharing the same machine's cores as the system under test.

**500 concurrent users, 45s, legitimate traffic (`SteadyUser`):**

```
20229 requests, 0 failures (0.00%)
450-500 req/s sustained
p50: 3ms   p95: 15ms   p99: 170ms   max: 240ms
```

**1000 concurrent users, 45s, legitimate traffic:**

```
23449 requests, 1 failure (0.00%)
783-900 req/s sustained
p50: 100ms   p95: 200ms   p99: 260ms   max: 424ms
```

Throughput plateaus rather than scaling linearly between the two runs, and
latency at 1000 users is roughly 30x higher than at 500 for less than 2x
the throughput gain. `docker stats` during the 1000-user run identifies
the bottleneck:

```
gateway1   106% CPU   68MiB
gateway2   107% CPU   68MiB
gateway3   106% CPU   68MiB
postgres    78% CPU   146MiB
upstream    34% CPU   32MiB
nginx       19% CPU   25MiB
redis       10% CPU   20MiB
```

All three gateway containers are CPU-saturated (single uvicorn worker each,
no multiprocessing), and Postgres is at 78% CPU from the synchronous
audit-log insert issued on every request (`_log_audit`, via
`BackgroundTasks`). Redis — the component doing the actual rate-limit
math — is at 10%. At this scale the bottleneck is the application tier and
audit logging, not the rate limiter; addressing it would mean running
multiple uvicorn workers per gateway process, batching the audit-log
writes, or both.

**300 concurrent users, all over their configured limit, 30s
(`BurstyUser`):** the rejection path skips the upstream call, so it
sustains higher throughput than the accept path:

```
45132 requests, 44735 rejected with 429 (99.12% — by design, these
clients share only 5 API keys to force over-limit traffic)
1509 req/s sustained
p50: 170ms   p99: 250ms
```
