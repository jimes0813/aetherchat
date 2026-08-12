from __future__ import annotations

from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services.agent_benchmark_eval import (  # noqa: E402
    evaluate_agent_benchmarks,
    load_benchmark_cases,
)

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "agent_benchmark_cases.json"
_META, BENCHMARK_CASES = load_benchmark_cases(FIXTURE_PATH)


@pytest.mark.parametrize(
    "case",
    BENCHMARK_CASES,
    ids=[str(item["id"]) for item in BENCHMARK_CASES],
)
def test_agent_benchmark_case(case: dict):
    report = evaluate_agent_benchmarks(cases=[case])
    assert report.total == 1
    result = report.results[0]
    assert result.matched, f"{result.id}: {result.detail}"


def test_agent_benchmark_suite_shape():
    assert _META.get("schema_version") == 1
    assert _META.get("count") == 60
    assert len(BENCHMARK_CASES) == 60
    categories = {item["category"] for item in BENCHMARK_CASES}
    assert len(categories) >= 20
    from collections import Counter

    counts = Counter(item["category"] for item in BENCHMARK_CASES)
    assert all(value >= 3 for value in counts.values())
    ids = [item["id"] for item in BENCHMARK_CASES]
    assert len(ids) == len(set(ids))


def test_agent_benchmark_eval_runner_full_pass():
    report = evaluate_agent_benchmarks(fixture_path=FIXTURE_PATH)
    payload = report.to_dict()
    assert payload["summary"]["total"] == 60
    assert payload["summary"]["matched"] == 60
    assert payload["summary"]["mismatches"] == 0
    assert payload["summary"]["category_count"] >= 20
    assert payload["summary"]["min_per_category_ok"] is True
    assert payload["summary"]["pass"] is True
