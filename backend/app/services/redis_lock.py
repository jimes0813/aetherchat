# backend/app/services/redis_lock.py
import time
from contextlib import asynccontextmanager

import redis.asyncio as redis


class RedisLock:
    def __init__(self, redis_client: redis.Redis, key: str, timeout: int = 30):
        self.redis = redis_client
        self.key = f"lock:{key}"
        self.timeout = timeout
        self.lock_value = str(time.time())

    async def acquire(self) -> bool:
        if not self.redis:
            return True  # 无 Redis 时跳过锁
        acquired = await self.redis.set(self.key, self.lock_value, nx=True, ex=self.timeout)
        return bool(acquired)

    async def release(self):
        if not self.redis:
            return
        current = await self.redis.get(self.key)
        if current and current.decode() == self.lock_value:
            await self.redis.delete(self.key)

@asynccontextmanager
async def with_lock(redis_client: redis.Redis | None, key: str, timeout: int = 30):
    lock = RedisLock(redis_client, key, timeout)
    acquired = await lock.acquire()
    if not acquired:
        raise Exception(f"Failed to acquire lock for {key}")
    try:
        yield
    finally:
        await lock.release()
