"""Gates for offline knowledge RAG Recall@K evaluation."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

os.environ.setdefault("AETHERCHAT_DATA_DIR", str(PROJECT_ROOT / ".tmp-rag-eval"))

from app.services.knowledge_rag_eval import (  # noqa: E402
    evaluate_knowledge_rag,
    load_rag_fixture,
)

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "knowledge_rag_cases.json"


def test_fixture_shape():
    fixture = load_rag_fixture(FIXTURE)
    assert fixture["schema_version"] == 1
    assert len(fixture["corpus"]) >= 6
    assert len(fixture["cases"]) >= 10
    assert any(case.get("polarity") == "negative" for case in fixture["cases"])


def test_knowledge_rag_eval_passes_gates():
    report = evaluate_knowledge_rag(FIXTURE)
    payload = report.to_dict()
    assert payload["summary"]["case_count"] == len(load_rag_fixture(FIXTURE)["cases"])
    assert payload["summary"]["hit_rate_at_3"] >= 0.85
    assert payload["summary"]["mrr"] >= 0.75
    assert payload["passed"] is True, payload["gate_results"]


def test_positive_case_records_citations():
    report = evaluate_knowledge_rag(FIXTURE)
    positives = [item for item in report.cases if item.polarity == "positive"]
    assert positives
    assert any(item.top_citations for item in positives)
    assert any(item.first_relevant_rank for item in positives)
