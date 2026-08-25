from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.http_client import close_http_client
from app.redis_client import close_redis
from app.routers import admin, gateway, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_redis()
    await close_http_client()


app = FastAPI(title="Distributed Rate Limiter + API Gateway", lifespan=lifespan)

app.include_router(gateway.router)
app.include_router(admin.router)
app.include_router(health.router)


@app.get("/")
async def root():
    settings = get_settings()
    return {"service": "rate-limiter-gateway", "instance_id": settings.instance_id}
