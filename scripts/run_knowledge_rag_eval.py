#!/usr/bin/env python3
"""CLI for offline knowledge RAG Recall@K evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.knowledge_rag_eval import evaluate_knowledge_rag  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "knowledge_rag_cases.json",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    report = evaluate_knowledge_rag(fixture_path=args.fixture)
    payload = report.to_dict()
    summary = payload["summary"]
    print("Knowledge RAG evaluation (offline hybrid retrieval)")
    print(f"  fixture: {payload['meta'].get('path')}")
    print(f"  schema_version: {payload['meta'].get('schema_version')}")
    print(f"  cases: {summary['case_count']} (pos={summary['positive_count']} neg={summary['negative_count']})")
    print(f"  corpus docs: {summary['corpus_count']}")
    print(f"  hit_rate@1: {summary['hit_rate_at_1']}")
    print(f"  hit_rate@3: {summary['hit_rate_at_3']}")
    print(f"  hit_rate@5: {summary['hit_rate_at_5']}")
    print(f"  recall@3: {summary['recall_at_3']}")
    print(f"  MRR: {summary['mrr']}")
    print(f"  negative_hit_rate@3: {summary['negative_hit_rate_at_3']}")
    print(f"  elapsed_ms: {payload['elapsed_ms']}")
    print(f"  gates: {payload['gate_results']}")
    print(f"  PASSED: {payload['passed']}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  wrote: {args.json_out}")

    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
