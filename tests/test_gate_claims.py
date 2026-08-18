from __future__ import annotations

import json
from pathlib import Path

from scripts.check_gate_claims import _parse_electron, _parse_mypy

ROOT = Path(__file__).resolve().parents[1]


def test_parse_mypy_success_and_errors():
    assert _parse_mypy("Success: no issues found in 79 source files") == (79, 0)
    assert _parse_mypy("Found 1 error in 1 file (checked 80 source files)") == (1, 1)


def test_parse_electron_node_test_summary():
    output = "# tests 24\n# pass 24\n# fail 0\n"
    assert _parse_electron(output) == (24, 24)


def test_claims_snapshot_exists_and_has_required_keys():
    claims = json.loads(
        (ROOT / "docs" / "evidence" / "quality_gate_claims.json").read_text(encoding="utf-8")
    )
    assert "mypy" in claims and "electron" in claims
    assert {"files", "issues"} <= set(claims["mypy"])
    assert {"passed", "total"} <= set(claims["electron"])
