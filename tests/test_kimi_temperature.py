"""Moonshot/Kimi models currently require temperature=1 only."""

from __future__ import annotations

from app.models.schemas import ProviderConfig
from app.providers.base import OpenAICompatProvider


def _provider(**kwargs) -> OpenAICompatProvider:
    base = {
        "name": "kimi",
        "type": "openai_compat",
        "model": "kimi-k3",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_ref": "KIMI_API_KEY",
        "max_tokens": 256,
        "participation_policy": "always",
    }
    base.update(kwargs)
    return OpenAICompatProvider(ProviderConfig(**base))


def test_kimi_effective_temperature_forced_to_one() -> None:
    provider = _provider()
    assert provider._is_moonshot_kimi() is True
    assert provider._effective_temperature(0) == 1.0
    assert provider._effective_temperature(0.7) == 1.0
    assert provider._effective_temperature(1) == 1.0


def test_moonshot_model_id_detected_without_kimi_name() -> None:
    provider = _provider(name="research", model="moonshot-v1-128k")
    assert provider._is_moonshot_kimi() is True
    assert provider._effective_temperature(0.2) == 1.0


def test_non_kimi_provider_keeps_requested_temperature() -> None:
    provider = _provider(
        name="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        api_key_ref="DEEPSEEK_API_KEY",
    )
    assert provider._is_moonshot_kimi() is False
    assert provider._effective_temperature(0) == 0.0
    assert provider._effective_temperature(0.7) == 0.7
    assert provider._effective_temperature(None) == 0.7
