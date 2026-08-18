from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_kpi_evidence",
    PROJECT_ROOT / "scripts" / "generate_kpi_evidence.py",
)
generate_kpi_evidence = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(generate_kpi_evidence)


def test_kpi_classification_never_promotes_proxy_or_small_samples() -> None:
    items = generate_kpi_evidence.build_kpi_items(
        declaration={
            "summary": {
                "total": 100,
                "mismatches": 0,
                "false_verified_error_green": 0,
            }
        },
        benchmark={
            "summary": {"total": 60, "matched": 60, "category_count": 20}
        },
        rag={"passed": True, "summary": {"hit_rate_at_3": 1.0, "mrr": 1.0}},
        telemetry={
            "status": "ready",
            "recovery": {
                "sample_sufficient": False,
                "success_rate": 1.0,
                "meets_target": True,
            },
        },
        release={
            "summary": {
                "local_dev_ready": True,
                "public_rc_ready": False,
                "public_1_0_go_no_go": "No-Go",
                "public_rc_missing": 4,
            }
        },
    )
    by_id = {item["id"]: item for item in items}

    assert by_id["error_green_rate"]["status"] == "pass"
    assert by_id["controlled_task_closure_rate"]["status"] == "insufficient_evidence"
    assert by_id["recovery_success_rate"]["status"] == "insufficient_evidence"
    assert by_id["public_release_gate"]["status"] == "no_go"
    assert by_id["knowledge_rag_quality"]["status"] == "pass"


def test_checksum_manifest_excludes_previous_manifest(tmp_path: Path) -> None:
    payload = tmp_path / "kpi-evidence.json"
    payload.write_text('{"status":"pass"}\n', encoding="utf-8")
    stale_manifest = tmp_path / "SHA256SUMS.json"
    stale_manifest.write_text('{"stale":true}\n', encoding="utf-8")

    manifest = generate_kpi_evidence._build_checksum_manifest(tmp_path)

    assert manifest == {
        payload.name: hashlib.sha256(payload.read_bytes()).hexdigest(),
    }
    stale_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert generate_kpi_evidence._build_checksum_manifest(tmp_path) == manifest
