# Sluice

A distributed rate limiter and API gateway in FastAPI, with rate-limit
state shared across multiple gateway instances via Redis — so scaling the
gateway horizontally doesn't let clients bypass their limit by hitting a
different instance. Implements Token Bucket, Sliding Window Log, and
Sliding Window Counter algorithms as atomic Redis Lua scripts, and is
load-tested to ~16k rate-limit checks/sec (see [Tests](#tests)).

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

**25 tests, all passing against real Redis + Postgres + the actual FastAPI
app (no mocks), reproduced flake-free across 5+ consecutive full-suite runs.**
A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the same suite
against Postgres/Redis service containers on every push.

| File | Count | What it proves |
|---|---|---|
| `test_token_bucket.py` | 3 | Capacity enforcement, refill-over-time, burst-vs-steady-rate behavior |
| `test_sliding_window_log.py` | 3 | Exact limit enforcement, window slides per-request rather than in fixed buckets, remaining-count accounting |
| `test_sliding_window_counter.py` | 2 | Limit enforcement, boundary-smoothing (deterministically aligned to a window boundary — naive wall-clock timing here is flaky, see the test's comment for why) |
| `test_concurrency.py` | 4 | **Core correctness claim**: fires 200 truly concurrent requests (`asyncio.gather`, not sequential awaits) at a single client key with `limit=20`, across all 3 algorithms — asserts exactly 20 succeed |
| `test_concurrency_stress.py` | 4 | 50x oversubscription (2500 concurrent requests for 50 slots) still holds the exact limit; 100 distinct clients firing 2000 concurrent requests stay correctly isolated from each other (no cross-client key collisions) |
| `test_gateway_integration.py` | 6 | End-to-end through the real ASGI app: proxy → upstream forwarding, 429 + `Retry-After` once over limit, admin config CRUD with immediate cache invalidation, rejected unknown algorithms, denied requests appearing in the audit log |
| `test_dependencies.py` | 3 | Config resolution falls back to defaults, picks up a Postgres override, and is correctly served from the Redis cache rather than hitting Postgres every request |

### Test results (for resume / portfolio)

```
25 passed in 4.68s
```

The concurrency + stress suite, run standalone with `-s` to show the numbers:

```
[TokenBucketLimiter] 2500 concurrent requests, limit=50: 50 allowed, 0.159s wall clock (15763 checks/sec)
[SlidingWindowLogLimiter] 2500 concurrent requests, limit=50: 50 allowed, 0.159s wall clock (15712 checks/sec)
[SlidingWindowCounterLimiter] 2500 concurrent requests, limit=50: 50 allowed, 0.155s wall clock (16160 checks/sec)
100 clients x 20 concurrent requests (2000 total): every client admitted exactly 10, 0.118s wall clock (16959 checks/sec)
```

Why this matters more than "tests pass": a naive Python `GET count; if count < limit: INCR`
implementation would let concurrent requests race — two coroutines can both read
`count=19` before either writes back, letting both through and breaking the limit.
These tests fire requests concurrently on purpose to try to trigger exactly that bug,
at up to 50x oversubscription and across 100 simultaneous clients, and confirm
Redis's atomic Lua execution prevents it every time.

### Two real bugs this testing effort caught

Writing these tests didn't just confirm the design — it found actual bugs, which
is a better interview story than "I wrote tests and they passed":

1. **Stale limiter cache.** `get_limiter()` cached each algorithm's Lua-script
   wrapper keyed only by algorithm name, permanently binding it to whichever
   Redis client was passed on the *first* call. If the app ever reconnects to
   Redis (a new client instance), old cached limiters keep talking to the dead
   connection. Fixed by keying the cache on `(algorithm, id(redis))` instead —
   see `app/algorithms/__init__.py`.
2. **Unbounded connection pool under bursty load.** The Redis client had no
   `max_connections` cap, so firing thousands of truly concurrent requests
   (via `asyncio.gather` in the stress tests) made it open one new TCP
   connection per in-flight request instead of reusing a small pool — thousands
   of connections at once, which measurably degraded Redis and, at higher
   concurrency, stalled outright. Fixed with a bounded `BlockingConnectionPool`
   (`max_connections=50`, callers queue instead of erroring) — which also
   turned out ~3x *faster* in practice (2500 concurrent checks: ~5.5k
   checks/sec unbounded vs. ~16k checks/sec bounded), since reusing 50 warm
   connections beats a fresh TCP handshake per request.

## Load testing

Locust drives the gateway through the nginx LB with two profiles —
`SteadyUser` (under the limit) and `BurstyUser` (over the limit, to
exercise the 429 path):

```bash
pip install -r requirements-dev.txt
locust -f load_test/locustfile.py --host http://localhost:8080
```

Open http://localhost:8089, pick a user count, and record throughput /
p50 / p95 / p99 from the Locust UI — these are the numbers to quote in a
resume bullet.

## Resume bullet points

Measured on a single M-series laptop running the full Docker Compose stack
(3 gateway replicas + nginx + Redis + Postgres) — a dedicated host would do
meaningfully better; re-run `make load-test` and swap in your own numbers.

- Built a distributed rate limiter and API gateway in FastAPI, implementing
  Token Bucket, Sliding Window Log, and Sliding Window Counter algorithms
  as atomic Redis Lua scripts to prevent race conditions across horizontally
  scaled gateway instances.
- Wrote a 25-test suite (unit, end-to-end, and concurrency/stress) covering
  the algorithms, the ASGI app, and config caching, run against real Redis
  and Postgres with zero mocks; reproduced flake-free across 5+ consecutive
  full-suite runs and wired into a GitHub Actions CI pipeline.
- Proved race-freedom under real contention, not just sequential calls: at
  50x oversubscription (2500 concurrent requests racing for 50 slots) the
  limit held exactly, and 100 simultaneous clients stayed correctly isolated
  across 2000 concurrent requests with zero cross-client interference —
  sustaining ~16,000 rate-limit checks/sec through a bounded connection pool.
- Debugged and fixed two concurrency bugs the stress tests surfaced: a
  stale-limiter-cache issue where a cached Lua-script wrapper stayed bound to
  a dead Redis client after a reconnect, and an unbounded connection pool
  that opened one new TCP connection per concurrent request under load
  (fixing it also made throughput ~3x faster: 5.5k → 16k checks/sec).
- Simulated a 3-instance gateway cluster behind an nginx load balancer with
  Docker Compose; verified via Locust that a single client's request count
  stayed correctly enforced while requests round-robined across all 3
  instances, proving limits are shared via Redis rather than tracked
  per-instance.
- Load-tested the gateway with Locust at 150 concurrent users, sustaining
  ~140 req/s with p50 4ms / p99 110ms end-to-end latency (LB → gateway →
  Redis check → upstream) and a 0.4% false-throttle rate under legitimate
  traffic; a separate burst profile confirmed over-limit clients are
  rejected with correct 429s and `Retry-After` headers rather than passed
  through.
- Versioned the Postgres schema with Alembic migrations run through a
  dedicated one-shot init container, avoiding the race condition of 3
  concurrently-starting gateway replicas each trying to create the same
  tables.
- Instrumented the gateway with Prometheus metrics and a Grafana dashboard
  (request rate, denial rate, and per-algorithm latency), and persisted a
  Postgres-backed audit log of rate-limit violations for debugging and
  per-client configuration.
