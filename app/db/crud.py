from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RateLimitConfig, RequestLog


async def get_config(session: AsyncSession, client_id: str) -> RateLimitConfig | None:
    result = await session.execute(
        select(RateLimitConfig).where(RateLimitConfig.client_id == client_id)
    )
    return result.scalar_one_or_none()


async def upsert_config(
    session: AsyncSession,
    client_id: str,
    algorithm: str,
    limit: int,
    window_seconds: int,
    burst: int | None,
) -> RateLimitConfig:
    config = await get_config(session, client_id)
    if config is None:
        config = RateLimitConfig(client_id=client_id)
        session.add(config)
    config.algorithm = algorithm
    config.limit = limit
    config.window_seconds = window_seconds
    config.burst = burst
    await session.commit()
    await session.refresh(config)
    return config


async def delete_config(session: AsyncSession, client_id: str) -> bool:
    config = await get_config(session, client_id)
    if config is None:
        return False
    await session.delete(config)
    await session.commit()
    return True


async def log_request(
    session: AsyncSession,
    client_id: str,
    path: str,
    method: str,
    algorithm: str,
    allowed: bool,
    remaining: int,
    instance_id: str,
) -> None:
    session.add(
        RequestLog(
            client_id=client_id,
            path=path,
            method=method,
            algorithm=algorithm,
            allowed=allowed,
            remaining=remaining,
            instance_id=instance_id,
        )
    )
    await session.commit()


async def list_violations(session: AsyncSession, limit: int = 100) -> list[RequestLog]:
    result = await session.execute(
        select(RequestLog)
        .where(RequestLog.allowed.is_(False))
        .order_by(RequestLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
