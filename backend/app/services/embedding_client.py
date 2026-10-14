"""OpenAI-compatible network embeddings with local-hash fallback support.

Configuration (env):
  AETHERCHAT_EMBEDDING_PROVIDER=openai|compat|local-hash
  AETHERCHAT_EMBEDDING_MODEL=text-embedding-3-small | embedding-3 | ...
  AETHERCHAT_EMBEDDING_BASE_URL=https://api.openai.com/v1
  AETHERCHAT_EMBEDDING_API_KEY=...  (or vault ref AETHERCHAT_EMBEDDING_API_KEY)

When provider is openai/compat and env fields are incomplete, the client
auto-picks from the model registry vault keys: OpenAI first, then Zhipu/GLM
(known /embeddings support), then other openai_compat entries only if
AETHERCHAT_EMBEDDING_MODEL is set.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("aetherchat.embeddings")

LOCAL_HASH_PROVIDER = "local-hash"
LOCAL_HASH_MODEL = "blake2b-256d"
LOCAL_HASH_DIMENSIONS = 256

DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_ZHIPU_MODEL = "embedding-3"

# Host → default embedding model when auto-resolving.
_HOST_MODEL_DEFAULTS: list[tuple[re.Pattern[str], str, int]] = [
    (re.compile(r"(api\.openai\.com|openai\.azure\.com)", re.I), DEFAULT_OPENAI_MODEL, 1536),
    (re.compile(r"(bigmodel\.cn|zhipu)", re.I), DEFAULT_ZHIPU_MODEL, 2048),
]


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str  # openai | compat | local-hash
    model: str
    base_url: str | None
    api_key: str | None
    source: str  # how config was resolved (env / vault:glm / local-hash / ...)
    expected_dimensions: int | None = None

    @property
    def uses_network(self) -> bool:
        return self.provider in {"openai", "compat"} and bool(self.api_key) and bool(self.base_url)

    @property
    def label(self) -> str:
        if not self.uses_network:
            return f"{LOCAL_HASH_PROVIDER}-{LOCAL_HASH_DIMENSIONS}d"
        dims = f"{self.expected_dimensions}d" if self.expected_dimensions else "?"
        return f"{self.provider}:{self.model}:{dims}"


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str
    model: str
    dimensions: int
    network: bool
    warning: str | None = None


def _normalize_base_url(url: str) -> str:
    value = str(url or "").strip().rstrip("/")
    if not value:
        return DEFAULT_OPENAI_BASE
    # Accept both .../v1 and bare host roots.
    return value


def _embeddings_url(base_url: str) -> str:
    base = _normalize_base_url(base_url)
    if base.endswith("/embeddings"):
        return base
    return f"{base}/embeddings"


def _host_defaults(base_url: str | None) -> tuple[str, int | None]:
    host = urlparse(base_url or "").netloc or (base_url or "")
    for pattern, model, dims in _HOST_MODEL_DEFAULTS:
        if pattern.search(host) or pattern.search(base_url or ""):
            return model, dims
    return DEFAULT_OPENAI_MODEL, None


def _load_models_json() -> list[dict[str, Any]]:
    data_dir = os.environ.get("AETHERCHAT_DATA_DIR")
    candidates: list[Path] = []
    if data_dir:
        candidates.append(Path(data_dir).expanduser() / "models.json")
    # Packaged Electron path (active install on this machine).
    candidates.append(
        Path.home() / ".config" / "aetherchat-frontend" / "backend-data" / "models.json"
    )
    candidates.append(Path.home() / ".local" / "share" / "aetherchat" / "models.json")
    for path in candidates:
        try:
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    return [item for item in raw if isinstance(item, dict)]
        except Exception:
            continue
    return []


def _vault_key(ref: str) -> str | None:
    if not ref:
        return None
    env_direct = os.getenv(ref)
    if env_direct:
        return env_direct
    try:
        from .vault import vault

        return vault.get_key(ref)
    except Exception:
        return None


def resolve_embedding_config() -> EmbeddingConfig:
    """Resolve embedding provider from env + vault/model registry."""
    provider = (os.getenv("AETHERCHAT_EMBEDDING_PROVIDER") or "openai").strip().lower()
    if provider in {"local", "hash", "local_hash"}:
        provider = "local-hash"
    if provider not in {"openai", "compat", "local-hash"}:
        provider = "openai"

    if provider == "local-hash":
        return EmbeddingConfig(
            provider=LOCAL_HASH_PROVIDER,
            model=LOCAL_HASH_MODEL,
            base_url=None,
            api_key=None,
            source="env:local-hash",
            expected_dimensions=LOCAL_HASH_DIMENSIONS,
        )

    env_model = (os.getenv("AETHERCHAT_EMBEDDING_MODEL") or "").strip() or None
    env_base = (os.getenv("AETHERCHAT_EMBEDDING_BASE_URL") or "").strip() or None
    env_key = (os.getenv("AETHERCHAT_EMBEDDING_API_KEY") or "").strip() or None
    if not env_key:
        env_key = _vault_key("AETHERCHAT_EMBEDDING_API_KEY")

    if env_key and (env_base or env_model):
        base = _normalize_base_url(env_base or DEFAULT_OPENAI_BASE)
        model, dims = (env_model, None)
        if not model:
            model, dims = _host_defaults(base)
        return EmbeddingConfig(
            provider="compat" if provider == "compat" else "openai",
            model=model,
            base_url=base,
            api_key=env_key,
            source="env",
            expected_dimensions=dims,
        )

    if env_key and not env_base:
        # Key only → assume OpenAI.
        return EmbeddingConfig(
            provider="openai",
            model=env_model or DEFAULT_OPENAI_MODEL,
            base_url=DEFAULT_OPENAI_BASE,
            api_key=env_key,
            source="env:openai-default",
            expected_dimensions=1536,
        )

    # Auto from model registry.
    models = _load_models_json()

    def pick(
        predicate,
        *,
        default_base: str | None,
        default_model: str,
        dims: int | None,
        source: str,
    ) -> EmbeddingConfig | None:
        for item in models:
            if not predicate(item):
                continue
            ref = str(item.get("api_key_ref") or "")
            key = _vault_key(ref) if ref else None
            if not key:
                continue
            base = str(item.get("base_url") or default_base or "").strip() or default_base
            if not base:
                continue
            model = env_model or default_model
            return EmbeddingConfig(
                provider="compat",
                model=model,
                base_url=_normalize_base_url(base),
                api_key=key,
                source=source,
                expected_dimensions=dims,
            )
        return None

    # 1) OpenAI
    chosen = pick(
        lambda m: (
            str(m.get("api_key_ref") or "").upper() in {"OPENAI_API_KEY", "MODEL_KEY_GPT"}
            or (str(m.get("name") or "").lower() in {"gpt", "openai"} and m.get("type") == "openai_compat")
        ),
        default_base=DEFAULT_OPENAI_BASE,
        default_model=env_model or DEFAULT_OPENAI_MODEL,
        dims=1536,
        source="vault:openai",
    )
    if chosen:
        return chosen

    # 2) Zhipu / GLM — documented OpenAI-compatible embeddings.
    chosen = pick(
        lambda m: (
            "bigmodel.cn" in str(m.get("base_url") or "").lower()
            or str(m.get("name") or "").lower() in {"glm", "zhipu", "智谱"}
        ),
        default_base=DEFAULT_ZHIPU_BASE,
        default_model=env_model or DEFAULT_ZHIPU_MODEL,
        dims=2048,
        source="vault:zhipu",
    )
    if chosen:
        return chosen

    # 3) Any openai_compat with key only when model is explicitly set.
    if env_model:
        for item in models:
            if item.get("type") not in {"openai_compat", "xai"}:
                continue
            ref = str(item.get("api_key_ref") or "")
            key = _vault_key(ref) if ref else None
            base = str(item.get("base_url") or "").strip()
            if not key or not base:
                continue
            host_model, dims = _host_defaults(base)
            return EmbeddingConfig(
                provider="compat",
                model=env_model or host_model,
                base_url=_normalize_base_url(base),
                api_key=key,
                source=f"vault:{item.get('name')}",
                expected_dimensions=dims,
            )

    # No usable network config → local-hash (caller still may force network attempt).
    return EmbeddingConfig(
        provider=LOCAL_HASH_PROVIDER,
        model=LOCAL_HASH_MODEL,
        base_url=None,
        api_key=None,
        source="fallback:local-hash",
        expected_dimensions=LOCAL_HASH_DIMENSIONS,
    )


def _l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def vectors_to_blob(values: list[float]) -> bytes:
    return array("f", values).tobytes()


def blob_to_vector(blob: bytes) -> list[float]:
    stored = array("f")
    stored.frombytes(blob)
    return list(stored)


def local_hash_vector(text: str, dimensions: int = LOCAL_HASH_DIMENSIONS) -> list[float]:
    """Deterministic offline embedding (blake2b feature hashing)."""
    import hashlib

    normalized = " ".join(str(text).casefold().split())
    words = re.findall(r"[a-z0-9_]{2,}|[\u3400-\u9fff]", normalized)
    compact = re.sub(r"\s+", "", normalized)
    ngrams = [compact[index : index + 3] for index in range(max(0, len(compact) - 2))]
    features = words + ngrams[:6000]
    values = [0.0] * dimensions
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        number = int.from_bytes(digest, "little")
        index = number % dimensions
        values[index] += 1.0 if number & (1 << 63) else -1.0
    return _l2_normalize(values)


def _parse_embedding_response(payload: dict[str, Any], batch_size: int) -> list[list[float]]:
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("embedding response missing data[]")
    ordered = sorted(rows, key=lambda item: int(item.get("index", 0)))
    vectors: list[list[float]] = []
    for item in ordered:
        emb = item.get("embedding")
        if not isinstance(emb, list) or not emb:
            raise RuntimeError("embedding row missing embedding[]")
        vectors.append(_l2_normalize([float(x) for x in emb]))
    if len(vectors) != batch_size:
        # Some providers omit index / return single vector for string input.
        if batch_size == 1 and len(vectors) >= 1:
            return vectors[:1]
        raise RuntimeError(f"expected {batch_size} embeddings, got {len(vectors)}")
    return vectors


def embed_texts_network(
    texts: list[str],
    config: EmbeddingConfig,
    *,
    timeout: float = 45.0,
    client: httpx.Client | None = None,
) -> EmbeddingResult:
    if not texts:
        return EmbeddingResult([], config.provider, config.model, 0, True)
    if not config.uses_network:
        raise RuntimeError("network embedding config incomplete")

    url = _embeddings_url(config.base_url or "")
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    # OpenAI-compat: input may be string or array of strings.
    body: dict[str, Any] = {
        "model": config.model,
        "input": texts if len(texts) > 1 else texts[0],
    }
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.post(url, headers=headers, json=body)
        if response.status_code >= 400:
            detail = response.text[:400]
            raise RuntimeError(f"embedding HTTP {response.status_code}: {detail}")
        payload = response.json()
        vectors = _parse_embedding_response(payload, len(texts))
        dims = len(vectors[0]) if vectors else 0
        model = str(payload.get("model") or config.model)
        return EmbeddingResult(vectors, config.provider, model, dims, True)
    finally:
        if owns_client:
            http.close()


def embed_texts(
    texts: list[str],
    *,
    config: EmbeddingConfig | None = None,
    client: httpx.Client | None = None,
    allow_local_fallback: bool = True,
) -> EmbeddingResult:
    """Embed texts via network when configured; soft-fallback to local-hash."""
    cfg = config or resolve_embedding_config()
    if not texts:
        dims = cfg.expected_dimensions or LOCAL_HASH_DIMENSIONS
        return EmbeddingResult([], cfg.provider if cfg.uses_network else LOCAL_HASH_PROVIDER, cfg.model, dims, cfg.uses_network)

    if cfg.uses_network:
        try:
            return embed_texts_network(texts, cfg, client=client)
        except Exception as exc:
            warning = f"network embedding failed ({exc}); using {LOCAL_HASH_PROVIDER}"
            logger.warning("aetherchat.embeddings fallback: %s", warning)
            if not allow_local_fallback:
                raise
            vectors = [local_hash_vector(text) for text in texts]
            return EmbeddingResult(
                vectors,
                LOCAL_HASH_PROVIDER,
                LOCAL_HASH_MODEL,
                LOCAL_HASH_DIMENSIONS,
                False,
                warning=warning,
            )

    vectors = [local_hash_vector(text) for text in texts]
    return EmbeddingResult(
        vectors,
        LOCAL_HASH_PROVIDER,
        LOCAL_HASH_MODEL,
        LOCAL_HASH_DIMENSIONS,
        False,
    )


def embed_query(text: str, **kwargs: Any) -> EmbeddingResult:
    result = embed_texts([text], **kwargs)
    return result
