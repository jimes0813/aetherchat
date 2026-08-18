import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.execution_strategy import resolve_execution_strategy  # noqa: E402


def test_explicit_planned_strategy_is_persisted_for_project_mode():
    action = {"mode": "project", "execution_strategy": "planned"}

    resolved = resolve_execution_strategy(action)

    assert resolved == {
        "mode": "project",
        "requested": "planned",
        "effective": "planned",
        "source": "explicit",
    }
    assert action["requested_execution_strategy"] == "planned"
    assert action["effective_execution_strategy"] == "planned"


def test_legacy_request_resolves_to_observable_direct_compatibility_contract():
    action = {"mode": "project"}

    resolved = resolve_execution_strategy(action)

    assert resolved == {
        "mode": "project",
        "requested": None,
        "effective": "direct",
        "source": "compatibility_default",
    }
    assert action["execution_strategy"] == "direct"
    assert action["execution_strategy_source"] == "compatibility_default"


def test_untrusted_derived_fields_do_not_override_a_new_explicit_request():
    action = {
        "mode": "project",
        "execution_strategy": "direct",
        "effective_execution_strategy": "planned",
        "execution_strategy_source": "compatibility_default",
    }

    resolved = resolve_execution_strategy(action)

    assert resolved["effective"] == "direct"
    assert resolved["source"] == "explicit"
    assert action["effective_execution_strategy"] == "direct"


def test_persisted_contract_can_be_restored_without_reinferring_its_source():
    action = {
        "mode": "project",
        "requested_execution_strategy": None,
        "execution_strategy": "direct",
        "effective_execution_strategy": "direct",
        "execution_strategy_source": "compatibility_default",
    }

    resolved = resolve_execution_strategy(action)

    assert resolved["requested"] is None
    assert resolved["effective"] == "direct"
    assert resolved["source"] == "compatibility_default"


@pytest.mark.parametrize(
    ("action", "message"),
    [
        ({"mode": "unknown"}, "不支持的运行模式"),
        (
            {"mode": "project", "execution_strategy": "automatic"},
            "不支持的执行策略",
        ),
        (
            {"mode": "casual", "execution_strategy": "planned"},
            "仅适用于 project",
        ),
        (
            {
                "mode": "project",
                "requested_execution_strategy": "planned",
                "execution_strategy": "direct",
                "effective_execution_strategy": "direct",
                "execution_strategy_source": "explicit",
            },
            "请求执行策略与实际执行策略不一致",
        ),
    ],
)
def test_invalid_execution_contracts_are_rejected(action, message):
    with pytest.raises(ValueError, match=message):
        resolve_execution_strategy(action)
