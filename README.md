# Sluice

[![CI](https://github.com/Pradeep24032004/sluice---a-distributed-rate-limiter-and-API-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/Pradeep24032004/sluice---a-distributed-rate-limiter-and-API-gateway/actions/workflows/ci.yml)

A distributed rate limiter and API gateway in FastAPI, with rate-limit
state shared across multiple gateway instances via Redis — so scaling the
gateway horizontally doesn't let clients bypass their limit by hitting a
different instance. Implements Token Bucket, Sliding Window Log, and
Sliding Window Counter algorithms as atomic Redis Lua scripts, and is
stress-tested up to 50,000 concurrent requests and 1,000 simultaneous
clients with the limit holding exactly every time (see [Tests](#tests)).

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

**30 tests, all passing against real Redis + Postgres + the actual FastAPI
app (no mocks), reproduced flake-free across 5+ consecutive full-suite runs
(30/30 every time).** A GitHub Actions workflow (`.github/workflows/ci.yml`)
runs the same suite against Postgres/Redis service containers on every push.

| File | Count | What it proves |
|---|---|---|
| `test_token_bucket.py` | 3 | Capacity enforcement, refill-over-time, burst-vs-steady-rate behavior |
| `test_sliding_window_log.py` | 3 | Exact limit enforcement, window slides per-request rather than in fixed buckets, remaining-count accounting |
| `test_sliding_window_counter.py` | 2 | Limit enforcement, boundary-smoothing (deterministically aligned to a window boundary — naive wall-clock timing here is flaky, see the test's comment for why) |
| `test_concurrency.py` | 4 | **Core correctness claim**: fires 200 truly concurrent requests (`asyncio.gather`, not sequential awaits) at a single client key with `limit=20`, across all 3 algorithms — asserts exactly 20 succeed |
| `test_concurrency_stress.py` | 9 | Large-scale: 50x and 100x oversubscription (up to 20,000 concurrent requests for 200 slots), an extreme single-key run at **50,000 concurrent requests**, and multi-client isolation at up to **1,000 simultaneous clients / 20,000 concurrent requests** — every client gets exactly its own limit, no cross-client leakage |
| `test_gateway_integration.py` | 6 | End-to-end through the real ASGI app: proxy → upstream forwarding, 429 + `Retry-After` once over limit, admin config CRUD with immediate cache invalidation, rejected unknown algorithms, denied requests appearing in the audit log |
| `test_dependencies.py` | 3 | Config resolution falls back to defaults, picks up a Postgres override, and is correctly served from the Redis cache rather than hitting Postgres every request |

### Test results (for resume / portfolio)

```
30 passed in 13.75s
```

The concurrency + stress suite, run standalone with `-s` to show the numbers —
including the two headline large-scale runs:

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

Ran the full stress suite 3x back-to-back with **zero flakiness** — every run,
every scale (2,500 up through 50,000 concurrent requests), the limit held
*exactly*, and every one of the 1,000 simultaneous clients in the isolation
test was admitted *exactly* its own limit, no more, no less.

Why this matters more than "tests pass": a naive Python `GET count; if count < limit: INCR`
implementation would let concurrent requests race — two coroutines can both read
`count=19` before either writes back, letting both through and breaking the limit.
These tests fire requests concurrently on purpose to try to trigger exactly that bug,
at up to 100x oversubscription, 50,000 requests deep, and across 1,000 simultaneous
clients, and confirm Redis's atomic Lua execution prevents it every time.

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
# or headless, e.g.:
locust -f load_test/locustfile.py SteadyUser --host http://localhost:8080 --headless -u 1000 -r 100 --run-time 45s
```

All numbers below are from the full Docker Compose stack (3 gateway
replicas + nginx + Redis + Postgres + Locust) on a single M-series laptop —
everything, including the load generator, sharing one machine's cores.

**500 concurrent users, 45s, legitimate traffic (`SteadyUser`):**

```
20229 requests, 0 failures (0.00%)
450-500 req/s sustained
p50: 3ms   p95: 15ms   p99: 170ms   max: 240ms
```

**1000 concurrent users, 45s, legitimate traffic:** throughput plateaus
rather than scaling further — this is where I found the actual bottleneck:

```
23449 requests, 1 failure (0.00%)
783-900 req/s sustained
p50: 100ms   p95: 200ms   p99: 260ms   max: 424ms
```

Latency at 1000 users is ~30x higher than at 500 despite less than 2x the
throughput gain, so I checked `docker stats` mid-run instead of just
reporting the number:

```
gateway1   106% CPU   68MiB
gateway2   107% CPU   68MiB
gateway3   106% CPU   68MiB
postgres    78% CPU   146MiB
upstream    34% CPU   32MiB
nginx       19% CPU   25MiB
redis       10% CPU   20MiB
```

All 3 gateway containers are CPU-saturated (single uvicorn worker each,
no multiprocessing) and Postgres is at 78% — the synchronous audit-log
`INSERT` + commit on every request (`_log_audit`, via `BackgroundTasks`)
is real load, not free. Redis, the thing actually doing the atomic
rate-limit math, is barely touched at 10%. **The bottleneck at this scale
is the app tier and the audit log, not the rate limiter** — the correct
next steps would be more uvicorn workers per gateway, batching the audit
log writes, or both. That diagnosis is worth more in an interview than the
raw req/s number.

**300 concurrent users, all over their limit, 30s (`BurstyUser`):** the
*rejection* path is cheap — no upstream call, so it actually sustains
higher throughput than the accept path:

```
45132 requests, 44735 rejected with 429 (99.12% — as designed, these
clients share only 5 API keys on purpose to force over-limit traffic)
1509 req/s sustained
p50: 170ms   p99: 250ms
```

## Resume bullet points

Measured on a single M-series laptop running the full Docker Compose stack
(3 gateway replicas + nginx + Redis + Postgres) — a dedicated host would do
meaningfully better; re-run `make load-test` and swap in your own numbers.

### Copy-paste block (use these 4)

Leads with the *debugging story*, not the test count — "found and fixed a
real bug" reads stronger than "wrote N tests" to an interviewer, and every
number here is something you can defend live if asked "walk me through that."

- **Found and fixed a Redis connection-pool bug** via concurrency stress
  testing (50,000 concurrent requests on one key) that was silently opening
  one new TCP connection per in-flight request under load — degrading
  throughput 3x. Fixed with a bounded `BlockingConnectionPool`, measured
  5.5k → 16k checks/sec after.
- **Proved rate-limit correctness under real contention, not sequential
  calls**: verified the exact limit holds at 100x oversubscription
  (50,000 concurrent requests) and across 1,000 simultaneous clients
  (20,000 concurrent requests) with zero cross-client leakage, using
  `asyncio.gather` to force true concurrency in the test itself.
- **Diagnosed a load-test bottleneck with `docker stats`, not guesswork**:
  at 1,000 concurrent users, found the gateway containers CPU-saturated
  (106% each) and Postgres at 78% from synchronous audit-log writes — while
  Redis, the actual rate limiter, sat at 10%. Correctly isolated the
  bottleneck to the app tier, not the rate-limiting logic.
- Built a distributed rate limiter + API gateway (FastAPI, Redis, Postgres)
  implementing Token Bucket, Sliding Window Log, and Sliding Window Counter
  as atomic Redis Lua scripts; 30-test suite with zero mocks, reproduced
  flake-free across 5+ runs, wired into GitHub Actions CI.

### Full list (swap any of the above for these if more relevant)

- Built a distributed rate limiter and API gateway in FastAPI, implementing
  Token Bucket, Sliding Window Log, and Sliding Window Counter algorithms
  as atomic Redis Lua scripts to prevent race conditions across horizontally
  scaled gateway instances.
- Wrote a 30-test suite (unit, end-to-end, and concurrency/stress) covering
  the algorithms, the ASGI app, and config caching, run against real Redis
  and Postgres with zero mocks; reproduced flake-free across 5+ consecutive
  full-suite runs (30/30 every time) and wired into a GitHub Actions CI
  pipeline.
- Proved race-freedom under real contention at scale, not just sequential
  calls or small batches: verified the exact limit held at up to 100x
  oversubscription and 50,000 concurrent requests on a single key, and that
  1,000 simultaneous clients (20,000 concurrent requests total) stayed
  perfectly isolated from each other with zero cross-client leakage —
  sustaining ~15,000 rate-limit checks/sec throughout.
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
- Load-tested the gateway with Locust up to 1,000 concurrent users; at 500
  users sustained ~500 req/s with p50 3ms / p99 170ms and 0% false-throttle
  rate, and at 1,000 users diagnosed the actual bottleneck using `docker
  stats` rather than just reporting a number — found the gateway containers
  CPU-saturated (106% each, single uvicorn worker) and Postgres at 78% CPU
  from synchronous audit-log writes, while Redis (the rate limiter itself)
  sat at just 10%, correctly isolating the bottleneck to the app tier and
  audit logging rather than the rate-limiting logic.
- Verified the rejection path is cheap under a dedicated burst profile: 300
  over-limit clients sustained 1,509 req/s of correctly-rejected 429s with
  `Retry-After` headers — higher throughput than the accept path, since a
  429 skips the upstream call entirely.
- Versioned the Postgres schema with Alembic migrations run through a
  dedicated one-shot init container, avoiding the race condition of 3
  concurrently-starting gateway replicas each trying to create the same
  tables.
- Instrumented the gateway with Prometheus metrics and a Grafana dashboard
  (request rate, denial rate, and per-algorithm latency), and persisted a
  Postgres-backed audit log of rate-limit violations for debugging and
  per-client configuration.
