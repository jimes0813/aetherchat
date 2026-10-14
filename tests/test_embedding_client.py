"""Unit tests for OpenAI-compatible embedding client (HTTP mocked)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services import embedding_client as ec  # noqa: E402
from app.services.embedding_client import (  # noqa: E402
    EmbeddingConfig,
    embed_texts,
    embed_texts_network,
    local_hash_vector,
    resolve_embedding_config,
    vectors_to_blob,
)


def test_local_hash_deterministic_and_normalized():
    a = local_hash_vector("hello knowledge base")
    b = local_hash_vector("hello knowledge base")
    assert a == b
    assert len(a) == 256
    norm = sum(x * x for x in a) ** 0.5
    assert abs(norm - 1.0) < 1e-5


def test_resolve_prefers_env(monkeypatch):
    monkeypatch.setenv("AETHERCHAT_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("AETHERCHAT_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("AETHERCHAT_EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("AETHERCHAT_EMBEDDING_API_KEY", "test-key-not-real")
    cfg = resolve_embedding_config()
    assert cfg.uses_network
    assert cfg.model == "text-embedding-3-small"
    assert cfg.api_key == "test-key-not-real"
    assert "openai.com" in (cfg.base_url or "")


def test_embed_texts_network_parses_response():
    cfg = EmbeddingConfig(
        provider="openai",
        model="text-embedding-3-small",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        source="test",
        expected_dimensions=3,
    )
    payload = {
        "model": "text-embedding-3-small",
        "data": [
            {"index": 0, "embedding": [1.0, 0.0, 0.0]},
            {"index": 1, "embedding": [0.0, 1.0, 0.0]},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        assert request.headers.get("Authorization") == "Bearer test-key"
        body = json.loads(request.content.decode())
        assert body["model"] == "text-embedding-3-small"
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = embed_texts_network(["alpha", "beta"], cfg, client=client)
    assert result.network is True
    assert result.dimensions == 3
    assert len(result.vectors) == 2
    assert abs(sum(x * x for x in result.vectors[0]) - 1.0) < 1e-6


def test_embed_texts_falls_back_on_http_error(monkeypatch):
    monkeypatch.setenv("AETHERCHAT_EMBEDDING_PROVIDER", "openai")
    cfg = EmbeddingConfig(
        provider="openai",
        model="text-embedding-3-small",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        source="test",
        expected_dimensions=1536,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "quota"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = embed_texts(["fallback please"], config=cfg, client=client, allow_local_fallback=True)
    assert result.network is False
    assert result.provider == "local-hash"
    assert result.dimensions == 256
    assert result.warning
    assert len(result.vectors[0]) == 256


def test_vector_blob_roundtrip_length():
    vec = local_hash_vector("blob")
    blob = vectors_to_blob(vec)
    assert len(blob) == 256 * 4


def test_resolve_local_hash(monkeypatch):
    monkeypatch.setenv("AETHERCHAT_EMBEDDING_PROVIDER", "local-hash")
    for key in (
        "AETHERCHAT_EMBEDDING_API_KEY",
        "AETHERCHAT_EMBEDDING_BASE_URL",
        "AETHERCHAT_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = resolve_embedding_config()
    assert cfg.provider == "local-hash"
    assert not cfg.uses_network
