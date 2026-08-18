from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_evidence",
    PROJECT_ROOT / "scripts" / "release_evidence.py",
)
release_evidence = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(release_evidence)


def test_release_evidence_gate_reports_local_ready_and_public_no_go():
    report = release_evidence.evaluate_release_evidence(
        version="0.2.0",
        refresh_metadata=True,
    )
    summary = report["summary"]

    assert report["schema"] == "aetherchat-release-evidence-v1"
    assert summary["local_dev_ready"] is True
    assert summary["public_rc_ready"] is False
    assert summary["public_1_0_go_no_go"] == "No-Go"
    assert summary["local_dev_fail"] == 0
    assert summary["local_dev_missing"] == 0
    assert summary["public_rc_missing"] >= 4

    by_id = {item["id"]: item for item in report["checks"]}
    portable = (
        "ed25519_roundtrip",
        "workflow_preflight",
        "workflow_matrix",
        "execution_declarations",
        "agent_benchmarks",
        "knowledge_rag_recall",
    )
    for item_id in portable:
        assert by_id[item_id]["status"] == "pass", by_id[item_id]

    # Hosted CI has no linux-arm64 packages; those checks skip instead of failing.
    for item_id in (
        "artifact_appimage",
        "artifact_deb",
        "build_metadata",
        "sbom",
        "deb_chrome_sandbox",
    ):
        assert by_id[item_id]["status"] in {"pass", "skipped"}, by_id[item_id]

    # Without operator secrets in the environment, signed_rc_secrets stays missing.
    assert by_id["signed_rc_secrets"]["tier"] == "public_rc"
    assert by_id["signed_rc_secrets"]["status"] in {"missing", "pass"}

    for item_id in (
        "ci_three_platforms_green",
        "signed_update_manifest",
        "windows_install_upgrade_uninstall",
        "opt_install_sandbox_runtime",
    ):
        assert by_id[item_id]["status"] == "missing"
        assert by_id[item_id]["tier"] == "public_rc"


def test_release_evidence_skips_missing_local_packages(monkeypatch, tmp_path):
    missing = tmp_path / "empty-release"
    missing.mkdir()
    monkeypatch.setattr(release_evidence, "ROOT", tmp_path)
    (tmp_path / "frontend" / "release").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text(
        (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    for name in (
        "execution_verification_cases.json",
        "agent_benchmark_cases.json",
        "knowledge_rag_cases.json",
    ):
        source = PROJECT_ROOT / "tests" / "fixtures" / name
        (tmp_path / "tests" / "fixtures" / name).write_text(
            source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (tmp_path / "backend").mkdir()
    monkeypatch.setattr(release_evidence, "ROOT", tmp_path)
    report = release_evidence.evaluate_release_evidence(version="0.2.0")
    by_id = {item["id"]: item for item in report["checks"]}
    assert by_id["artifact_appimage"]["status"] == "skipped"
    assert by_id["artifact_deb"]["status"] == "skipped"
    assert by_id["workflow_preflight"]["status"] == "pass"
    assert report["summary"]["local_dev_ready"] is True
    assert report["summary"]["local_dev_missing"] == 0


def test_release_evidence_cli_check_writes_report(tmp_path, monkeypatch):
    out = tmp_path / "evidence.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "release_evidence.py",
            "check",
            "--refresh-metadata",
            "--json-out",
            str(out),
        ],
    )
    code = release_evidence.main()
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["local_dev_ready"] is True
    assert payload["summary"]["public_1_0_go_no_go"] == "No-Go"
