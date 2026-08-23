"""Redis-backed fixed-window rate limiting with an in-process fallback."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.core.config import settings
from app.core.errors import RateLimitError

_memory: dict[str, deque] = defaultdict(deque)


class RateLimiter:
    def __init__(self):
        self._redis = None

    async def _client(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(
                    settings.REDIS_URL, decode_responses=True
                )
            except Exception:  # noqa: BLE001
                self._redis = False  # fall back to memory
        return self._redis or None

    async def check(self, key: str, limit: int, window: int = 60) -> None:
        client = await self._client()
        if client is not None:
            try:
                bucket = f"rl:{key}:{int(time.time() // window)}"
                count = await client.incr(bucket)
                if count == 1:
                    await client.expire(bucket, window * 2)
                if count > limit:
                    raise RateLimitError(
                        f"Too many requests. Try again in {window} seconds."
                    )
                return
            except RateLimitError:
                raise
            except Exception:  # noqa: BLE001
                pass  # degrade to in-memory rather than failing open entirely

        now = time.time()
        window_start = now - window
        history = _memory[key]
        while history and history[0] < window_start:
            history.popleft()
        if len(history) >= limit:
            raise RateLimitError(f"Too many requests. Try again in {window} seconds.")
        history.append(now)


limiter = RateLimiter()


async def limit_requests(key: str, limit: int | None = None) -> None:
    await limiter.check(key, limit or settings.RATE_LIMIT_PER_MINUTE)


async def limit_auth(key: str) -> None:
    await limiter.check(f"auth:{key}", settings.AUTH_RATE_LIMIT_PER_MINUTE)
