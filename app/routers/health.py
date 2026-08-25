from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis

from app.config import get_settings
from app.redis_client import get_redis

router = APIRouter()


@router.get("/health")
async def health(redis: Redis = Depends(get_redis)):
    settings = get_settings()
    try:
        await redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {
        "status": "ok" if redis_ok else "degraded",
        "instance_id": settings.instance_id,
        "redis": redis_ok,
    }


@router.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
