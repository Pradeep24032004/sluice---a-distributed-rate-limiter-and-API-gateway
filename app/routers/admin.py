from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.algorithms import ALGORITHMS
from app.db.crud import delete_config, list_violations, upsert_config
from app.db.session import get_session
from app.dependencies import resolve_config
from app.redis_client import get_redis

router = APIRouter(prefix="/admin")


class RateLimitConfigIn(BaseModel):
    algorithm: str
    limit: int
    window_seconds: int
    burst: int | None = None


class RateLimitConfigOut(RateLimitConfigIn):
    client_id: str


@router.get("/limits/{client_id}", response_model=RateLimitConfigOut)
async def get_limit(client_id: str, redis: Redis = Depends(get_redis)):
    """Effective config for a client — their DB override if one exists,
    otherwise the app-wide defaults. Same resolution the gateway itself
    uses, so this always reflects what a real request would be limited to."""
    config = await resolve_config(client_id, redis)
    return RateLimitConfigOut(
        client_id=client_id,
        algorithm=config.algorithm,
        limit=config.limit,
        window_seconds=config.window_seconds,
        burst=config.burst,
    )


@router.put("/limits/{client_id}", response_model=RateLimitConfigOut)
async def set_limit(
    client_id: str,
    body: RateLimitConfigIn,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    if body.algorithm not in ALGORITHMS:
        raise HTTPException(400, f"Unknown algorithm. Choose one of: {list(ALGORITHMS)}")

    config = await upsert_config(
        session, client_id, body.algorithm, body.limit, body.window_seconds, body.burst
    )
    # invalidate the cached config so the new limit takes effect immediately
    await redis.delete(f"rl:config:{client_id}")
    return RateLimitConfigOut(
        client_id=config.client_id,
        algorithm=config.algorithm,
        limit=config.limit,
        window_seconds=config.window_seconds,
        burst=config.burst,
    )


@router.delete("/limits/{client_id}")
async def remove_limit(
    client_id: str,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    deleted = await delete_config(session, client_id)
    await redis.delete(f"rl:config:{client_id}")
    if not deleted:
        raise HTTPException(404, "No custom config for this client")
    return {"deleted": True}


@router.get("/violations")
async def get_violations(limit: int = 100, session: AsyncSession = Depends(get_session)):
    rows = await list_violations(session, limit)
    return [
        {
            "client_id": r.client_id,
            "path": r.path,
            "method": r.method,
            "algorithm": r.algorithm,
            "instance_id": r.instance_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/algorithms")
async def get_algorithms():
    return list(ALGORITHMS)
