#!/usr/bin/env python3
"""CLI for the deterministic Agent benchmark suite (20+ categories × 3)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.agent_benchmark_eval import evaluate_agent_benchmarks  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "agent_benchmark_cases.json",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.quiet:
        logging.disable(logging.CRITICAL)

    report = evaluate_agent_benchmarks(fixture_path=args.fixture)
    payload = report.to_dict()
    summary = payload["summary"]
    print("Agent benchmark evaluation")
    print(f"  fixture: {payload['meta'].get('path')}")
    print(f"  schema_version: {payload['meta'].get('schema_version')}")
    print(f"  total: {summary['total']}")
    print(f"  matched: {summary['matched']}")
    print(f"  mismatches: {summary['mismatches']}")
    print(f"  categories: {summary['category_count']}")
    print(f"  min_per_category_ok: {summary['min_per_category_ok']}")
    print(f"  duration_ms: {summary['duration_ms']}")
    if summary["mismatches"]:
        print(f"  mismatched_ids: {payload['mismatched_ids'][:20]}")
        for detail in payload["mismatch_details"][:10]:
            print(f"    - {detail['id']}: {detail['detail']}")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  wrote: {args.json_out}")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
