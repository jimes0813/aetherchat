from __future__ import annotations

import asyncio

from app.services.rate_limit import (
    VOICE_STATUS_LIMIT,
    allow_request,
    bucket_for,
    memory_allow,
    rate_limit_store,
)


def test_voice_paths_use_dedicated_buckets():
    assert bucket_for("/voice/status") == "voice-status"
    assert bucket_for("/voice/tts") == "voice-mutate"
    assert bucket_for("/voice/stt") == "voice-mutate"
    assert bucket_for("/models/") == "default"


def test_memory_allow_is_per_bucket():
    rate_limit_store.clear()
    assert memory_allow("127.0.0.1", "voice-status", limit=1)[0] is True
    allowed, retry_after = memory_allow("127.0.0.1", "voice-status", limit=1)
    assert allowed is False
    assert retry_after >= 1
    assert memory_allow("127.0.0.1", "default", limit=1)[0] is True


def test_allow_request_falls_back_when_redis_errors():
    class BrokenRedis:
        def pipeline(self):
            raise RuntimeError("redis down")

    rate_limit_store.clear()

    async def _run():
        first = await allow_request(BrokenRedis(), "10.0.0.1", "/voice/status")
        second = await allow_request(BrokenRedis(), "10.0.0.1", "/voice/status")
        return first, second

    first, second = asyncio.run(_run())
    assert first == (True, 0)
    # Default voice-status limit is high; force a tiny window via memory store
    # already populated by the fallback path.
    assert second[0] is True or VOICE_STATUS_LIMIT <= 1
