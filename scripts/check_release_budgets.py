#!/usr/bin/env python3
"""Fail release builds that regress AetherChat's installed-size budgets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MIB = 1024 * 1024


def tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unpacked", required=True, type=Path)
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--renderer", required=True, type=Path)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    measurements = {
        "installed_unpacked": (tree_size(args.unpacked), 450 * MIB),
        "backend": (tree_size(args.backend), 130 * MIB),
        "locales": (tree_size(args.unpacked / "locales"), 5 * MIB),
        "renderer_dist": (tree_size(args.renderer), 5 * MIB),
    }
    packages = list(args.release.glob("AetherChat-*.AppImage")) + list(
        args.release.glob("AetherChat-*.deb")
    )
    failures: list[str] = []
    report: dict[str, object] = {
        "schema": "aetherchat-release-size-budget-v1",
        "measurements": {},
        "packages": [],
    }
    for name, (actual, budget) in measurements.items():
        print(f"{name}: {actual / MIB:.1f} MiB / {budget / MIB:.0f} MiB")
        report["measurements"][name] = {
            "bytes": actual,
            "budget_bytes": budget,
            "within_budget": actual <= budget,
        }
        if actual > budget:
            failures.append(f"{name} exceeds budget")
    for package in packages:
        actual = package.stat().st_size
        budget = 220 * MIB
        print(f"{package.name}: {actual / MIB:.1f} MiB / {budget / MIB:.0f} MiB")
        report["packages"].append(
            {
                "name": package.name,
                "bytes": actual,
                "budget_bytes": budget,
                "within_budget": actual <= budget,
            }
        )
        if actual > budget:
            failures.append(f"{package.name} exceeds budget")
    report["within_budget"] = not failures
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if failures:
        raise SystemExit("; ".join(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
