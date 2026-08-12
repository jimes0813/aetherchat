#!/usr/bin/env python3
"""CLI for the versioned execution-declaration evaluation suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.execution_verification_eval import (  # noqa: E402
    evaluate_declarations,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "execution_verification_cases.json",
        help="Path to versioned declaration fixture",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to write full report JSON",
    )
    args = parser.parse_args()

    report = evaluate_declarations(fixture_path=args.fixture)
    payload = report.to_dict()
    summary = payload["summary"]
    print("Execution declaration evaluation")
    print(f"  fixture: {payload['meta'].get('path')}")
    print(f"  schema_version: {payload['meta'].get('schema_version')}")
    print(f"  total: {summary['total']}")
    print(f"  matched: {summary['matched']}")
    print(f"  mismatches: {summary['mismatches']}")
    print(f"  false_verified (错误绿色): {summary['false_verified_error_green']}")
    print(f"  false_reject: {summary['false_reject']}")
    print(f"  duration_ms: {summary['duration_ms']}")
    print(f"  status_matrix: {json.dumps(payload['status_matrix'], ensure_ascii=False)}")
    if summary["false_verified_error_green"]:
        print(f"  false_verified_ids: {payload['false_verified_ids']}")
    if summary["mismatches"]:
        print(f"  mismatched_ids: {payload['mismatched_ids'][:20]}")

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
