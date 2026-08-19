"""Public RC preflight + Ed25519 key generation helpers."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_generate_ed25519_keys_and_validate_pair():
    security = _load("release_security", PROJECT_ROOT / "scripts" / "release_security.py")
    private_b64, public_b64 = security.generate_ed25519_keypair_b64()
    assert private_b64 and public_b64
    security.validate_ed25519_pair_b64(private_b64, public_b64)


def test_public_rc_preflight_writes_reports(tmp_path, monkeypatch):
    preflight = _load("public_rc_preflight", PROJECT_ROOT / "scripts" / "public_rc_preflight.py")
    json_out = tmp_path / "preflight.json"
    md_out = tmp_path / "checklist.md"
    monkeypatch.setattr(
        "sys.argv",
        ["public_rc_preflight.py", "--json-out", str(json_out), "--md-out", str(md_out)],
    )
    monkeypatch.setenv("AETHERCHAT_RELEASE_KEYS_DIR", str(tmp_path / "empty-keys"))
    # Ensure no secrets required for a clean operator report
    for name in preflight.REQUIRED_SECRETS:
        monkeypatch.delenv(name, raising=False)
    code = preflight.main()
    assert code == 0
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["schema"] == "aetherchat-public-rc-preflight-v1"
    assert report["checklist"]["public_1_0_go_no_go"] == "No-Go"
    assert "linux_rc_ready" in report["checklist"]
    assert len(report["required_secret_names"]) == 4
    text = md_out.read_text(encoding="utf-8")
    assert "Public RC Preflight" in text
    assert "AETHERCHAT_RELEASE_PRIVATE_KEY_B64" in text
    assert "linux_rc_ready" in text


def test_linux_rc_ready_with_ed25519_without_windows(monkeypatch):
    preflight = _load("public_rc_preflight", PROJECT_ROOT / "scripts" / "public_rc_preflight.py")
    security = _load("release_security", PROJECT_ROOT / "scripts" / "release_security.py")
    private_b64, public_b64 = security.generate_ed25519_keypair_b64()
    monkeypatch.setenv("AETHERCHAT_RELEASE_PRIVATE_KEY_B64", private_b64)
    monkeypatch.setenv("AETHERCHAT_RELEASE_PUBLIC_KEY_B64", public_b64)
    monkeypatch.setenv("AETHERCHAT_RELEASE_KEYS_DIR", str(PROJECT_ROOT / "tests" / "fixtures" / "missing-release-keys"))
    for name in ("WINDOWS_CSC_LINK", "WINDOWS_CSC_KEY_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    # Simulate GitHub Ed25519-only secrets.
    monkeypatch.setattr(
        preflight,
        "_gh_secret_names",
        lambda: [
            "AETHERCHAT_RELEASE_PRIVATE_KEY_B64",
            "AETHERCHAT_RELEASE_PUBLIC_KEY_B64",
        ],
    )
    # local_dev evidence present
    evidence = {
        "summary": {"local_dev_ready": True},
    }
    evidence_path = PROJECT_ROOT / "frontend" / "release" / "release-evidence.local.json"
    original = evidence_path.read_text(encoding="utf-8") if evidence_path.is_file() else None
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    try:
        report = preflight.evaluate_public_rc_preflight()
        assert report["checklist"]["linux_rc_ready"] is True
        assert report["checklist"]["tag_ready"] is False
        assert report["checklist"]["github_windows_csc_ready"] is False
        assert any("Linux-first RC" in item for item in report["checklist"]["blockers"])
    finally:
        if original is None:
            if evidence_path.is_file():
                evidence_path.unlink()
        else:
            evidence_path.write_text(original, encoding="utf-8")


def test_offline_key_dir_fills_env_without_printing(tmp_path, monkeypatch, capsys):
    preflight = _load("public_rc_preflight", PROJECT_ROOT / "scripts" / "public_rc_preflight.py")
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    (key_dir / "AETHERCHAT_RELEASE_PRIVATE_KEY_B64.txt").write_text("offline-private\n")
    monkeypatch.setenv("AETHERCHAT_RELEASE_KEYS_DIR", str(key_dir))
    monkeypatch.delenv("AETHERCHAT_RELEASE_PRIVATE_KEY_B64", raising=False)
    preflight._load_offline_secrets()
    assert os.environ["AETHERCHAT_RELEASE_PRIVATE_KEY_B64"] == "offline-private"
    captured = capsys.readouterr()
    assert "offline-private" not in captured.out
    assert "offline-private" not in captured.err


def test_release_evidence_passes_secrets_when_env_pair_present(monkeypatch):
    security = _load("release_security", PROJECT_ROOT / "scripts" / "release_security.py")
    evidence = _load("release_evidence", PROJECT_ROOT / "scripts" / "release_evidence.py")
    private_b64, public_b64 = security.generate_ed25519_keypair_b64()
    monkeypatch.setenv("AETHERCHAT_RELEASE_PRIVATE_KEY_B64", private_b64)
    monkeypatch.setenv("AETHERCHAT_RELEASE_PUBLIC_KEY_B64", public_b64)
    monkeypatch.setenv("WINDOWS_CSC_LINK", "dummy-cert")
    monkeypatch.setenv("WINDOWS_CSC_KEY_PASSWORD", "dummy-pass")
    report = evidence.evaluate_release_evidence(version="0.2.0", refresh_metadata=False)
    by_id = {item["id"]: item for item in report["checks"]}
    assert by_id["signed_rc_secrets"]["status"] == "pass"
    # Still No-Go without CI green / published manifest
    assert report["summary"]["public_1_0_go_no_go"] == "No-Go"
