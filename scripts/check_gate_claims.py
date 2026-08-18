#!/usr/bin/env python3
"""Compare live quality-gate counters with the claims snapshot.

The snapshot is the machine-readable source of truth for remediations
reports. A stale docs/P0_P1_REMEDIATION_REPORT_*.md or a hand-edited
claims file fails the gate so HEAD and the report cannot drift.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_PATH = ROOT / "docs" / "evidence" / "quality_gate_claims.json"


def _latest_report() -> Path | None:
    reports = sorted(ROOT.joinpath("docs").glob("P0_P1_REMEDIATION_REPORT_*.md"))
    return reports[-1] if reports else None


def _load_claims() -> dict:
    if not CLAIMS_PATH.is_file():
        raise SystemExit(f"missing claims snapshot: {CLAIMS_PATH}")
    return json.loads(CLAIMS_PATH.read_text(encoding="utf-8"))


def _parse_mypy(text: str) -> tuple[int, int]:
    success = re.search(r"Success: no issues found in (\d+) source files", text)
    if success:
        return int(success.group(1)), 0
    files = re.search(r"Found (\d+) error(?:s)? in (\d+) file", text)
    if files:
        return int(files.group(2)), int(files.group(1))
    raise SystemExit(f"unable to parse mypy output:\n{text}")


def _parse_electron(text: str) -> tuple[int, int]:
    tests = re.search(r"^# tests (\d+)\s*$", text, re.MULTILINE)
    passed = re.search(r"^# pass (\d+)\s*$", text, re.MULTILINE)
    if tests and passed:
        return int(passed.group(1)), int(tests.group(1))
    # node --test compact summary: "ok N" / "tests N"
    alt_tests = re.search(r"^# (?:tests|test)s? (\d+)\s*$", text, re.MULTILINE)
    alt_pass = re.search(r"^# (?:pass|passed) (\d+)\s*$", text, re.MULTILINE)
    if alt_tests and alt_pass:
        return int(alt_pass.group(1)), int(alt_tests.group(1))
    raise SystemExit(f"unable to parse electron test output:\n{text}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mypy-output", default="")
    parser.add_argument("--electron-output", default="")
    parser.add_argument("--mypy-files", type=int)
    parser.add_argument("--mypy-issues", type=int)
    parser.add_argument("--electron-passed", type=int)
    parser.add_argument("--electron-total", type=int)
    args = parser.parse_args()

    claims = _load_claims()
    errors: list[str] = []

    if args.mypy_output:
        mypy_files, mypy_issues = _parse_mypy(args.mypy_output)
    elif args.mypy_files is not None and args.mypy_issues is not None:
        mypy_files, mypy_issues = args.mypy_files, args.mypy_issues
    else:
        raise SystemExit("mypy result is required")

    if args.electron_output:
        electron_passed, electron_total = _parse_electron(args.electron_output)
    elif args.electron_passed is not None and args.electron_total is not None:
        electron_passed, electron_total = args.electron_passed, args.electron_total
    else:
        raise SystemExit("electron result is required")

    expected_mypy = claims["mypy"]
    expected_electron = claims["electron"]
    if mypy_files != int(expected_mypy["files"]) or mypy_issues != int(expected_mypy["issues"]):
        errors.append(
            "mypy mismatch: live "
            f"{mypy_issues} issue(s) in {mypy_files} files, claims "
            f"{expected_mypy['issues']} issue(s) in {expected_mypy['files']} files. "
            f"Update {CLAIMS_PATH.relative_to(ROOT)} and the latest remediations report."
        )
    if (
        electron_passed != int(expected_electron["passed"])
        or electron_total != int(expected_electron["total"])
    ):
        errors.append(
            "electron mismatch: live "
            f"{electron_passed}/{electron_total}, claims "
            f"{expected_electron['passed']}/{expected_electron['total']}. "
            f"Update {CLAIMS_PATH.relative_to(ROOT)} and the latest remediations report."
        )

    report = _latest_report()
    if report is None:
        errors.append("no docs/P0_P1_REMEDIATION_REPORT_*.md found")
    else:
        body = report.read_text(encoding="utf-8")
        mypy_pat = re.compile(
            rf"Mypy.*{expected_mypy['files']}.*{expected_mypy['issues']}",
            re.IGNORECASE,
        )
        electron_pat = re.compile(
            rf"Electron.*{expected_electron['passed']}/{expected_electron['total']}",
            re.IGNORECASE,
        )
        if not mypy_pat.search(body):
            errors.append(
                f"{report.name} does not mention mypy "
                f"{expected_mypy['files']} files / {expected_mypy['issues']} issues"
            )
        if not electron_pat.search(body):
            errors.append(
                f"{report.name} does not mention Electron "
                f"{expected_electron['passed']}/{expected_electron['total']}"
            )

    if errors:
        print("quality-gate claims check FAILED:", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(
        "quality-gate claims OK: "
        f"mypy {mypy_issues} issue(s) in {mypy_files} files; "
        f"electron {electron_passed}/{electron_total}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
