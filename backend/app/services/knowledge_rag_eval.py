"""Offline knowledge RAG evaluation: fixed corpus + Recall@K / MRR gates.

Uses the local KnowledgeService only (FTS5 + hash vectors). No paid embeddings,
network, or production database.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .knowledge_service import KnowledgeService

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "knowledge_rag_cases.json"
)


def load_rag_fixture(path: Path | None = None) -> dict[str, Any]:
    fixture_path = path or DEFAULT_FIXTURE
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError(f"Unsupported knowledge RAG fixture shape: {fixture_path}")
    if not isinstance(raw.get("corpus"), list) or not raw["corpus"]:
        raise ValueError("Fixture must include a non-empty corpus list")
    raw = dict(raw)
    raw["_path"] = str(fixture_path)
    return raw


def _hit_matches(hit: dict[str, Any], relevant: dict[str, Any]) -> bool:
    source = str(hit.get("source_name") or "")
    expected_source = str(relevant.get("source_name") or "")
    if expected_source and source != expected_source:
        return False
    needle = str(relevant.get("content_contains") or "").strip()
    if needle:
        content = str(hit.get("content") or "")
        if needle.casefold() not in content.casefold():
            return False
    return bool(expected_source or needle)


def _first_relevant_rank(hits: Sequence[dict[str, Any]], relevant: Sequence[dict[str, Any]]) -> int | None:
    if not relevant:
        return None
    for index, hit in enumerate(hits, start=1):
        if any(_hit_matches(hit, item) for item in relevant):
            return index
    return None


def _recall_at_k(
    hits: Sequence[dict[str, Any]],
    relevant: Sequence[dict[str, Any]],
    k: int,
) -> float:
    if not relevant:
        return 1.0
    top = list(hits)[:k]
    matched = 0
    for item in relevant:
        if any(_hit_matches(hit, item) for hit in top):
            matched += 1
    return matched / len(relevant)


def _hit_at_k(
    hits: Sequence[dict[str, Any]],
    relevant: Sequence[dict[str, Any]],
    k: int,
) -> bool:
    if not relevant:
        return True
    top = list(hits)[:k]
    return any(
        _hit_matches(hit, item)
        for item in relevant
        for hit in top
    )


@dataclass
class CaseEval:
    id: str
    category: str
    polarity: str
    query: str
    hit_at: dict[str, bool] = field(default_factory=dict)
    recall_at: dict[str, float] = field(default_factory=dict)
    first_relevant_rank: int | None = None
    mrr: float = 0.0
    top_citations: list[str] = field(default_factory=list)
    strong_hit_count: int = 0
    ok: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "polarity": self.polarity,
            "query": self.query,
            "hit_at": self.hit_at,
            "recall_at": self.recall_at,
            "first_relevant_rank": self.first_relevant_rank,
            "mrr": self.mrr,
            "top_citations": self.top_citations,
            "strong_hit_count": self.strong_hit_count,
            "ok": self.ok,
            "notes": self.notes,
        }


@dataclass
class RagEvalReport:
    meta: dict[str, Any]
    summary: dict[str, Any]
    cases: list[CaseEval]
    gates: dict[str, Any]
    gate_results: dict[str, bool]
    elapsed_ms: float

    @property
    def passed(self) -> bool:
        return all(self.gate_results.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "summary": self.summary,
            "gates": self.gates,
            "gate_results": self.gate_results,
            "passed": self.passed,
            "elapsed_ms": self.elapsed_ms,
            "cases": [item.to_dict() for item in self.cases],
        }


def evaluate_knowledge_rag(
    fixture_path: Path | None = None,
    *,
    service: KnowledgeService | None = None,
) -> RagEvalReport:
    fixture = load_rag_fixture(fixture_path)
    k_values = [int(value) for value in (fixture.get("k_values") or [1, 3, 5])]
    search_limit = int(fixture.get("search_limit") or max(k_values + [8]))
    gates = dict(fixture.get("gates") or {})
    cases_raw = list(fixture["cases"])
    corpus = list(fixture["corpus"])

    own_service = service is None
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if service is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="aetherchat-rag-eval-")
        service = KnowledgeService(base_path=Path(temp_dir.name))

    session_id = str(uuid4())
    started = time.perf_counter()
    try:
        for doc in corpus:
            name = str(doc["name"])
            content = str(doc["content"]).encode("utf-8")
            service.create_attachment(
                session_id,
                name,
                "text/markdown" if name.endswith(".md") else "text/plain",
                content,
            )

        case_results: list[CaseEval] = []
        for case in cases_raw:
            query = str(case.get("query") or "")
            relevant = list(case.get("relevant") or [])
            polarity = str(case.get("polarity") or "positive")
            hits = service.search(session_id, query, limit=search_limit)
            rank = _first_relevant_rank(hits, relevant)
            mrr = (1.0 / rank) if rank else 0.0
            hit_at = {f"@{k}": _hit_at_k(hits, relevant, k) for k in k_values}
            recall_at = {f"@{k}": round(_recall_at_k(hits, relevant, k), 4) for k in k_values}
            strong = [hit for hit in hits[:3] if float(hit.get("score") or 0) >= 0.25]
            eval_case = CaseEval(
                id=str(case.get("id") or "unknown"),
                category=str(case.get("category") or ""),
                polarity=polarity,
                query=query,
                hit_at=hit_at,
                recall_at=recall_at,
                first_relevant_rank=rank,
                mrr=round(mrr, 4),
                top_citations=[str(hit.get("citation") or "") for hit in hits[:5]],
                strong_hit_count=len(strong),
            )
            if polarity == "positive" and relevant and not hit_at.get("@3", False):
                eval_case.ok = False
                eval_case.notes.append("positive case missed relevant source in top-3")
            if case.get("expect_no_strong_hit") and len(strong) > 0:
                # soft note only; gate uses aggregate negative hit rate
                eval_case.notes.append("negative case produced strong top-3 hits")
            case_results.append(eval_case)
    finally:
        if own_service and temp_dir is not None:
            temp_dir.cleanup()

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    positives = [item for item in case_results if item.polarity == "positive"]
    negatives = [item for item in case_results if item.polarity == "negative"]

    def avg_hit(k: int) -> float:
        if not positives:
            return 1.0
        key = f"@{k}"
        return sum(1 for item in positives if item.hit_at.get(key)) / len(positives)

    def avg_recall(k: int) -> float:
        if not positives:
            return 1.0
        key = f"@{k}"
        return sum(float(item.recall_at.get(key) or 0) for item in positives) / len(positives)

    mrr = (
        sum(item.mrr for item in positives) / len(positives)
        if positives else 1.0
    )
    # Negative "hit rate": fraction of negative cases with any strong top-3 hit
    negative_hit_rate = (
        sum(1 for item in negatives if item.strong_hit_count > 0) / len(negatives)
        if negatives else 0.0
    )

    summary = {
        "case_count": len(case_results),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "corpus_count": len(corpus),
        "hit_rate_at_1": round(avg_hit(1), 4),
        "hit_rate_at_3": round(avg_hit(3), 4),
        "hit_rate_at_5": round(avg_hit(5), 4),
        "recall_at_1": round(avg_recall(1), 4),
        "recall_at_3": round(avg_recall(3), 4),
        "recall_at_5": round(avg_recall(5), 4),
        "mrr": round(mrr, 4),
        "negative_hit_rate_at_3": round(negative_hit_rate, 4),
    }

    gate_results = {
        "min_hit_rate_at_1": summary["hit_rate_at_1"] >= float(gates.get("min_hit_rate_at_1", 0)),
        "min_hit_rate_at_3": summary["hit_rate_at_3"] >= float(gates.get("min_hit_rate_at_3", 0)),
        "min_hit_rate_at_5": summary["hit_rate_at_5"] >= float(gates.get("min_hit_rate_at_5", 0)),
        "min_mrr": summary["mrr"] >= float(gates.get("min_mrr", 0)),
        "max_negative_hit_rate_at_3": summary["negative_hit_rate_at_3"]
        <= float(gates.get("max_negative_hit_rate_at_3", 1)),
    }

    return RagEvalReport(
        meta={
            "schema_version": fixture.get("schema_version"),
            "name": fixture.get("name"),
            "description": fixture.get("description"),
            "path": fixture.get("_path"),
            "k_values": k_values,
        },
        summary=summary,
        cases=case_results,
        gates=gates,
        gate_results=gate_results,
        elapsed_ms=elapsed_ms,
    )
