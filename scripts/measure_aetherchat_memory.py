#!/usr/bin/env python3
"""Report aggregate PSS/USS for a running AetherChat process tree as JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def memory_for_pid(pid: int) -> tuple[int, int]:
    pss = private = 0
    for line in Path(f"/proc/{pid}/smaps_rollup").read_text(encoding="utf-8").splitlines():
        if line.startswith("Pss:"):
            pss = int(line.split()[1])
        elif line.startswith(("Private_Clean:", "Private_Dirty:")):
            private += int(line.split()[1])
    return pss, private


def process_tree(root_pid: int) -> set[int]:
    selected = {root_pid}
    while True:
        previous = len(selected)
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text(encoding="utf-8").split()
                if int(fields[3]) in selected:
                    selected.add(int(entry.name))
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                continue
        if len(selected) == previous:
            return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", default="AetherChat")
    parser.add_argument("--root-pid", type=int)
    parser.add_argument("--max-pss-mib", type=float, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = []
    selected = process_tree(args.root_pid) if args.root_pid else None
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8")
            if selected is not None:
                if pid not in selected:
                    continue
            elif args.match.lower() not in command.lower() and "aetherchat-backend" not in command.lower():
                continue
            pss, private = memory_for_pid(pid)
            rows.append({"pid": pid, "pss_kib": pss, "uss_kib": private})
        except (FileNotFoundError, PermissionError, ProcessLookupError, UnicodeDecodeError):
            continue
    total_pss = sum(row["pss_kib"] for row in rows)
    payload = {
        "schema": "aetherchat-memory-budget-v1",
        "processes": rows,
        "total_pss_mib": round(total_pss / 1024, 2),
        "total_uss_mib": round(sum(row["uss_kib"] for row in rows) / 1024, 2),
        "budget_pss_mib": args.max_pss_mib,
        "within_budget": total_pss <= args.max_pss_mib * 1024,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if payload["within_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
