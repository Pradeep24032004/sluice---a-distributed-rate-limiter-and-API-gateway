import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from redis.asyncio import Redis

from app.config import get_settings
from app.db.crud import log_request
from app.db.session import session_scope
from app.dependencies import get_client_id
from app.http_client import get_http_client
from app.metrics import GATEWAY_REQUESTS, UPSTREAM_LATENCY
from app.middleware.rate_limit import enforce_rate_limit
from app.redis_client import get_redis

router = APIRouter()


async def _log_audit(client_id, path, method, algorithm, allowed, remaining, instance_id):
    async with session_scope() as session:
        await log_request(
            session, client_id, path, method, algorithm, allowed, remaining, instance_id
        )


@router.api_route(
    "/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy(
    path: str,
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Depends(get_client_id),
    redis: Redis = Depends(get_redis),
):
    """Rate-limits the caller, then forwards the request to the upstream
    service if allowed. This is the gateway's core request path."""
    settings = get_settings()

    result, config = await enforce_rate_limit(client_id, redis)

    GATEWAY_REQUESTS.labels(
        algorithm=config.algorithm,
        allowed=str(result.allowed).lower(),
        instance_id=settings.instance_id,
    ).inc()
    background_tasks.add_task(
        _log_audit,
        client_id,
        path,
        request.method,
        config.algorithm,
        result.allowed,
        result.remaining,
        settings.instance_id,
    )

    if not result.allowed:
        return Response(
            content=f'{{"detail":"Rate limit exceeded","algorithm":"{config.algorithm}"}}',
            status_code=429,
            media_type="application/json",
            headers={
                "Retry-After": str(max(1, result.retry_after_ms // 1000)),
                "X-RateLimit-Limit": str(config.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Algorithm": config.algorithm,
                "X-Served-By": settings.instance_id,
            },
        )

    body = await request.body()
    start = time.perf_counter()
    upstream_response = await get_http_client().request(
        request.method,
        f"{settings.upstream_url}/{path}",
        params=request.query_params,
        content=body,
        headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
    )
    UPSTREAM_LATENCY.labels(instance_id=settings.instance_id).observe(
        time.perf_counter() - start
    )

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers={
            "X-RateLimit-Limit": str(config.limit),
            "X-RateLimit-Remaining": str(max(0, result.remaining)),
            "X-RateLimit-Algorithm": config.algorithm,
            "X-Served-By": settings.instance_id,
            "content-type": upstream_response.headers.get("content-type", "application/json"),
        },
    )
