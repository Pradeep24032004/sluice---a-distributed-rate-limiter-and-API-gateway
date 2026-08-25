from prometheus_client import Counter, Histogram

GATEWAY_REQUESTS = Counter(
    "gateway_requests_total",
    "Total requests seen by the gateway",
    ["algorithm", "allowed", "instance_id"],
)

RATE_LIMIT_DENIED = Counter(
    "rate_limit_denied_total",
    "Requests rejected by the rate limiter",
    ["algorithm", "instance_id"],
)

UPSTREAM_LATENCY = Histogram(
    "gateway_upstream_duration_seconds",
    "Latency of the proxied call to the upstream service",
    ["instance_id"],
)

RATE_LIMIT_CHECK_LATENCY = Histogram(
    "rate_limit_check_duration_seconds",
    "Latency of the Redis-backed rate-limit check itself",
    ["algorithm", "instance_id"],
)
