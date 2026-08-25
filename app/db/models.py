import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RateLimitConfig(Base):
    """Per-client rate-limit configuration. Falls back to the app defaults
    (app.config.Settings) when a client has no row here."""

    __tablename__ = "rate_limit_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    algorithm: Mapped[str] = mapped_column(String(64))
    limit: Mapped[int] = mapped_column(Integer)
    window_seconds: Mapped[int] = mapped_column(Integer)
    burst: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RequestLog(Base):
    """Audit trail of gateway decisions, used for the violations dashboard
    and for debugging why a specific client got throttled."""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), index=True)
    path: Mapped[str] = mapped_column(String(512))
    method: Mapped[str] = mapped_column(String(16))
    algorithm: Mapped[str] = mapped_column(String(64))
    allowed: Mapped[bool] = mapped_column(Boolean, index=True)
    remaining: Mapped[int] = mapped_column(Integer)
    instance_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
