from __future__ import annotations

import time
from collections import defaultdict

import redis.asyncio as redis

RATE_LIMIT = 60
RATE_WINDOW = 60
RATE_LIMIT_EXEMPT_PATHS = frozenset({"/health", "/health/ready"})
VOICE_MUTATING_LIMIT = 20
VOICE_STATUS_LIMIT = 60

rate_limit_store: defaultdict[str, list[float]] = defaultdict(list)


def bucket_for(path: str) -> str:
    if path.startswith(("/voice/stt", "/voice/tts")):
        return "voice-mutate"
    if path.startswith("/voice/"):
        return "voice-status"
    return "default"


def limit_for(bucket: str) -> int:
    if bucket == "voice-mutate":
        return VOICE_MUTATING_LIMIT
    if bucket == "voice-status":
        return VOICE_STATUS_LIMIT
    return RATE_LIMIT


def is_exempt(path: str, method: str) -> bool:
    return path in RATE_LIMIT_EXEMPT_PATHS or method == "OPTIONS"


def memory_allow(ip: str, bucket: str, *, limit: int, now: float | None = None) -> tuple[bool, int]:
    moment = time.time() if now is None else now
    key = f"{ip}:{bucket}"
    window = [stamp for stamp in rate_limit_store[key] if moment - stamp < RATE_WINDOW]
    if len(window) >= limit:
        rate_limit_store[key] = window
        retry_after = max(1, int(RATE_WINDOW - (moment - window[0])))
        return False, retry_after
    window.append(moment)
    rate_limit_store[key] = window
    return True, 0


async def redis_allow(
    client: redis.Redis,
    ip: str,
    bucket: str,
    *,
    limit: int,
    now: float | None = None,
) -> tuple[bool, int]:
    moment = time.time() if now is None else now
    key = f"aetherchat:rl:{ip}:{bucket}"
    window_start = moment - RATE_WINDOW
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zcard(key)
    results = await pipe.execute()
    count = int(results[1] or 0)
    if count >= limit:
        oldest = await client.zrange(key, 0, 0, withscores=True)
        oldest_score = float(oldest[0][1]) if oldest else moment
        return False, max(1, int(RATE_WINDOW - (moment - oldest_score)))
    member = f"{moment:.6f}:{count}"
    pipe = client.pipeline()
    pipe.zadd(key, {member: moment})
    pipe.expire(key, RATE_WINDOW)
    await pipe.execute()
    return True, 0


async def allow_request(
    redis_client: redis.Redis | None,
    ip: str,
    path: str,
    *,
    now: float | None = None,
    default_limit: int | None = None,
) -> tuple[bool, int]:
    bucket = bucket_for(path)
    limit = default_limit if bucket == "default" and default_limit is not None else limit_for(bucket)
    if redis_client is not None:
        try:
            return await redis_allow(redis_client, ip, bucket, limit=limit, now=now)
        except Exception:
            pass
    return memory_allow(ip, bucket, limit=limit, now=now)
