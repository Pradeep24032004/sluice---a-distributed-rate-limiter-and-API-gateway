from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 50
    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"

    upstream_url: str = "http://localhost:9000"

    default_algorithm: str = "token_bucket"  # token_bucket | sliding_window_log | sliding_window_counter
    default_limit: int = 20
    default_window_seconds: int = 10
    default_burst: int = 20  # token bucket capacity

    config_cache_ttl_seconds: int = 5  # how long a client's rate-limit config is cached in Redis

    instance_id: str = "gateway-1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
