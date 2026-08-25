from dataclasses import dataclass
from pathlib import Path

from redis.asyncio import Redis

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_ms: int
    algorithm: str


class LuaRateLimiter:
    """Base class for a rate-limiting algorithm backed by an atomic Lua script.

    Subclasses set `script_filename` and implement `build_args`. The script's
    SHA is cached after the first load; EVALSHA is retried as EVAL on a
    NOSCRIPT error (e.g. after a Redis restart flushes the script cache).
    """

    script_filename: str
    name: str

    def __init__(self, redis: Redis):
        self._redis = redis
        self._sha: str | None = None
        self._script_text = (SCRIPTS_DIR / self.script_filename).read_text()

    async def _eval(self, keys: list[str], args: list) -> list:
        if self._sha is None:
            self._sha = await self._redis.script_load(self._script_text)
        try:
            return await self._redis.evalsha(self._sha, len(keys), *keys, *args)
        except Exception as exc:  # redis raises a generic ResponseError on NOSCRIPT
            if "NOSCRIPT" not in str(exc):
                raise
            self._sha = await self._redis.script_load(self._script_text)
            return await self._redis.evalsha(self._sha, len(keys), *keys, *args)

    async def check(self, client_key: str, limit: int, window_seconds: int, **kwargs) -> RateLimitResult:
        raise NotImplementedError
