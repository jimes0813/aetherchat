#!/usr/bin/env python3
"""WP-0 local release-evidence gate.

Separates offline-verifiable local_dev evidence from public_rc items that still
require signed multi-platform CI. Never reports public 1.0 Go without remote
proof.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _load_release_security():
    path = ROOT / "scripts" / "release_security.py"
    spec = importlib.util.spec_from_file_location("release_security", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_security = _load_release_security()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(
    item_id: str,
    title: str,
    *,
    status: str,
    tier: str,
    detail: str,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "title": title,
        "status": status,
        "tier": tier,
        "detail": detail,
        "evidence": evidence or [],
    }


def refresh_linux_arm64_metadata(version: str = "0.2.0") -> list[str]:
    release_dir = ROOT / "frontend" / "release"
    appimage = release_dir / f"AetherChat-{version}-linux-arm64.AppImage"
    deb = release_dir / f"AetherChat-{version}-linux-arm64.deb"
    if not appimage.is_file() or not deb.is_file():
        raise FileNotFoundError("linux-arm64 AppImage/Deb not found under frontend/release")

    cdx = release_dir / f"AetherChat-{version}-linux-arm64.cdx.json"
    meta = release_dir / f"AetherChat-{version}-linux-arm64.build.json"
    ts = _now()
    release_security.command_sbom(
        SimpleNamespace(
            npm_lock=str(ROOT / "frontend" / "package-lock.json"),
            requirements=str(ROOT / "backend" / "requirements.txt"),
            version=version,
            timestamp=ts,
            output=str(cdx),
        )
    )
    release_security.command_metadata(
        SimpleNamespace(
            version=version,
            platform="linux",
            arch="arm64",
            timestamp=ts,
            artifact=[str(appimage), str(deb), str(cdx)],
            output=str(meta),
        )
    )
    return [str(cdx), str(meta)]


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_release_evidence(
    *,
    version: str = "0.2.0",
    refresh_metadata: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    release_dir = ROOT / "frontend" / "release"
    appimage = release_dir / f"AetherChat-{version}-linux-arm64.AppImage"
    deb = release_dir / f"AetherChat-{version}-linux-arm64.deb"
    meta_path = release_dir / f"AetherChat-{version}-linux-arm64.build.json"
    cdx_path = release_dir / f"AetherChat-{version}-linux-arm64.cdx.json"

    if refresh_metadata and appimage.is_file() and deb.is_file():
        refresh_linux_arm64_metadata(version)

    for path, label in ((appimage, "AppImage"), (deb, "Deb")):
        if path.is_file() and path.stat().st_size > 1_000_000:
            checks.append(
                _check(
                    f"artifact_{label.lower()}",
                    f"Linux ARM64 {label} package present",
                    status="pass",
                    tier="local_dev",
                    detail=f"{path.name} size={path.stat().st_size}",
                    evidence=[str(path)],
                )
            )
        else:
            checks.append(
                _check(
                    f"artifact_{label.lower()}",
                    f"Linux ARM64 {label} package present",
                    status="missing",
                    tier="local_dev",
                    detail=f"missing or too small: {path}",
                )
            )

    meta = _load_json(meta_path)
    if not meta:
        checks.append(
            _check(
                "build_metadata",
                "Build metadata exists and matches package hashes",
                status="missing",
                tier="local_dev",
                detail=f"missing {meta_path.name}",
            )
        )
    else:
        expected = {
            item["name"]: item
            for item in meta.get("artifacts", [])
            if isinstance(item, dict) and item.get("name")
        }
        mismatches: list[str] = []
        for path in (appimage, deb, cdx_path):
            if not path.is_file():
                mismatches.append(f"missing file {path.name}")
                continue
            entry = expected.get(path.name)
            if not entry:
                mismatches.append(f"metadata missing {path.name}")
                continue
            actual = _sha256(path)
            if actual != entry.get("sha256"):
                mismatches.append(f"{path.name} sha256 mismatch")
            if int(entry.get("size") or -1) != path.stat().st_size:
                mismatches.append(f"{path.name} size mismatch")
        checks.append(
            _check(
                "build_metadata",
                "Build metadata exists and matches package hashes",
                status="pass" if not mismatches else "fail",
                tier="local_dev",
                detail="ok" if not mismatches else "; ".join(mismatches),
                evidence=[str(meta_path)],
            )
        )

    cdx = _load_json(cdx_path)
    if cdx and cdx.get("bomFormat") == "CycloneDX" and cdx.get("components"):
        checks.append(
            _check(
                "sbom",
                "CycloneDX SBOM present with components",
                status="pass",
                tier="local_dev",
                detail=f"components={len(cdx.get('components') or [])}",
                evidence=[str(cdx_path)],
            )
        )
    else:
        checks.append(
            _check(
                "sbom",
                "CycloneDX SBOM present with components",
                status="missing",
                tier="local_dev",
                detail=f"invalid or missing {cdx_path.name}",
            )
        )

    if deb.is_file():
        try:
            listing = subprocess.check_output(
                ["dpkg-deb", "-c", str(deb)],
                text=True,
                stderr=subprocess.STDOUT,
            )
            sandbox_lines = [line for line in listing.splitlines() if "chrome-sandbox" in line]
            ok = any("rws" in line and "root/root" in line for line in sandbox_lines)
            checks.append(
                _check(
                    "deb_chrome_sandbox",
                    "Deb archives chrome-sandbox as root setuid",
                    status="pass" if ok else "fail",
                    tier="local_dev",
                    detail=sandbox_lines[0] if sandbox_lines else "chrome-sandbox not found in deb",
                    evidence=sandbox_lines[:2],
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                _check(
                    "deb_chrome_sandbox",
                    "Deb archives chrome-sandbox as root setuid",
                    status="fail",
                    tier="local_dev",
                    detail=f"dpkg-deb failed: {exc}",
                )
            )
    else:
        checks.append(
            _check(
                "deb_chrome_sandbox",
                "Deb archives chrome-sandbox as root setuid",
                status="missing",
                tier="local_dev",
                detail="deb missing",
            )
        )

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private = Ed25519PrivateKey.generate()
        private_pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public_pem = private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        with tempfile.TemporaryDirectory(prefix="aether-release-evidence-") as tmp:
            tmp_path = Path(tmp)
            package = tmp_path / "AetherChat-evidence.AppImage"
            package.write_bytes(b"release-evidence-payload")
            os.environ["AETHERCHAT_EVIDENCE_PRIVATE"] = base64.b64encode(private_pem).decode()
            os.environ["AETHERCHAT_EVIDENCE_PUBLIC"] = base64.b64encode(public_pem).decode()
            manifest = tmp_path / "manifest.json"
            envelope = tmp_path / "signed.json"
            release_security.command_manifest(
                SimpleNamespace(
                    version=version,
                    timestamp=_now(),
                    release=[f"linux,arm64,{package},https://updates.example.test/{package.name}"],
                    output=str(manifest),
                )
            )
            release_security.command_sign(
                SimpleNamespace(
                    manifest=str(manifest),
                    output=str(envelope),
                    key_env="AETHERCHAT_EVIDENCE_PRIVATE",
                )
            )
            release_security.command_verify(
                SimpleNamespace(
                    envelope=str(envelope),
                    key_env="AETHERCHAT_EVIDENCE_PUBLIC",
                )
            )
            data = json.loads(envelope.read_text(encoding="utf-8"))
            data["manifest"]["version"] = "9.9.9"
            envelope.write_text(json.dumps(data), encoding="utf-8")
            tamper_failed = False
            try:
                release_security.command_verify(
                    SimpleNamespace(
                        envelope=str(envelope),
                        key_env="AETHERCHAT_EVIDENCE_PUBLIC",
                    )
                )
            except SystemExit:
                tamper_failed = True
            checks.append(
                _check(
                    "ed25519_roundtrip",
                    "Ed25519 manifest sign/verify/tamper-reject works offline",
                    status="pass" if tamper_failed else "fail",
                    tier="local_dev",
                    detail="valid verify ok; tamper rejected" if tamper_failed else "tamper was accepted",
                )
            )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _check(
                "ed25519_roundtrip",
                "Ed25519 manifest sign/verify/tamper-reject works offline",
                status="fail",
                tier="local_dev",
                detail=str(exc),
            )
        )
    finally:
        os.environ.pop("AETHERCHAT_EVIDENCE_PRIVATE", None)
        os.environ.pop("AETHERCHAT_EVIDENCE_PUBLIC", None)

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    secrets = [
        "WINDOWS_CSC_LINK",
        "WINDOWS_CSC_KEY_PASSWORD",
        "AETHERCHAT_RELEASE_PRIVATE_KEY_B64",
        "AETHERCHAT_RELEASE_PUBLIC_KEY_B64",
    ]
    workflow_ok = (
        "release-preflight:" in workflow
        and "needs: release-preflight" in workflow
        and all(name in workflow for name in secrets)
        and "Signed release is blocked; missing secret(s)" in workflow
    )
    checks.append(
        _check(
            "workflow_preflight",
            "Release workflow fails fast without signing secrets",
            status="pass" if workflow_ok else "fail",
            tier="local_dev",
            detail="preflight + four secrets wired" if workflow_ok else "workflow contract incomplete",
            evidence=[".github/workflows/release.yml"],
        )
    )

    matrix_ok = all(
        token in workflow
        for token in ("linux-x64", "linux-arm64", "windows-x64", "ubuntu-24.04", "windows-2025")
    )
    checks.append(
        _check(
            "workflow_matrix",
            "CI matrix declares Linux x64/ARM64 and Windows x64",
            status="pass" if matrix_ok else "fail",
            tier="local_dev",
            detail="matrix targets present" if matrix_ok else "matrix targets incomplete",
            evidence=[".github/workflows/release.yml"],
        )
    )

    # Evaluation gates
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.services.execution_verification_eval import evaluate_declarations

        decl = evaluate_declarations(
            fixture_path=ROOT / "tests" / "fixtures" / "execution_verification_cases.json"
        )
        decl_ok = decl.mismatches == 0 and decl.false_verified == 0 and decl.total >= 100
        checks.append(
            _check(
                "execution_declarations",
                "100 execution declarations with zero error-green",
                status="pass" if decl_ok else "fail",
                tier="local_dev",
                detail=(
                    f"total={decl.total} matched={decl.matched} "
                    f"false_verified={decl.false_verified}"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _check(
                "execution_declarations",
                "100 execution declarations with zero error-green",
                status="fail",
                tier="local_dev",
                detail=str(exc),
            )
        )

    try:
        from app.services.agent_benchmark_eval import evaluate_agent_benchmarks

        bench = evaluate_agent_benchmarks(
            fixture_path=ROOT / "tests" / "fixtures" / "agent_benchmark_cases.json"
        )
        payload = bench.to_dict()["summary"]
        bench_ok = bool(payload.get("pass"))
        checks.append(
            _check(
                "agent_benchmarks",
                "20×3 agent benchmarks fully matched",
                status="pass" if bench_ok else "fail",
                tier="local_dev",
                detail=(
                    f"total={payload.get('total')} matched={payload.get('matched')} "
                    f"categories={payload.get('category_count')}"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _check(
                "agent_benchmarks",
                "20×3 agent benchmarks fully matched",
                status="fail",
                tier="local_dev",
                detail=str(exc),
            )
        )

    try:
        from app.services.knowledge_rag_eval import evaluate_knowledge_rag

        rag = evaluate_knowledge_rag(
            fixture_path=ROOT / "tests" / "fixtures" / "knowledge_rag_cases.json"
        )
        rag_payload = rag.to_dict()
        rag_summary = rag_payload["summary"]
        checks.append(
            _check(
                "knowledge_rag_recall",
                "Offline knowledge RAG Recall@K / MRR gates",
                status="pass" if rag_payload.get("passed") else "fail",
                tier="local_dev",
                detail=(
                    f"hit@3={rag_summary.get('hit_rate_at_3')} "
                    f"mrr={rag_summary.get('mrr')} "
                    f"neg@3={rag_summary.get('negative_hit_rate_at_3')}"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _check(
                "knowledge_rag_recall",
                "Offline knowledge RAG Recall@K / MRR gates",
                status="fail",
                tier="local_dev",
                detail=str(exc),
            )
        )

    # Public RC: secrets may be present in the operator environment without being
    # published yet. Detect presence without logging values; GitHub publication
    # remains a separate missing item unless gh reports all secrets.
    env_secret_names = [
        "WINDOWS_CSC_LINK",
        "WINDOWS_CSC_KEY_PASSWORD",
        "AETHERCHAT_RELEASE_PRIVATE_KEY_B64",
        "AETHERCHAT_RELEASE_PUBLIC_KEY_B64",
    ]
    env_present = {name: bool((os.environ.get(name) or "").strip()) for name in env_secret_names}
    env_all = all(env_present.values())
    pair_ok = False
    if env_present["AETHERCHAT_RELEASE_PRIVATE_KEY_B64"] and env_present["AETHERCHAT_RELEASE_PUBLIC_KEY_B64"]:
        try:
            release_security.validate_ed25519_pair_b64(
                os.environ["AETHERCHAT_RELEASE_PRIVATE_KEY_B64"].strip(),
                os.environ["AETHERCHAT_RELEASE_PUBLIC_KEY_B64"].strip(),
            )
            pair_ok = True
        except Exception:  # noqa: BLE001
            pair_ok = False

    if env_all and pair_ok:
        checks.append(
            _check(
                "signed_rc_secrets",
                "Production Ed25519 + Windows CSC secrets configured for tag release",
                status="pass",
                tier="public_rc",
                detail="all four secret env vars present and Ed25519 pair validates (values not logged)",
            )
        )
    else:
        missing_env = [name for name, ok in env_present.items() if not ok]
        detail = (
            f"missing env: {', '.join(missing_env)}"
            if missing_env
            else "Ed25519 pair present but failed validation"
        )
        detail += "; configure GitHub secrets then re-run public_rc_preflight.py"
        checks.append(
            _check(
                "signed_rc_secrets",
                "Production Ed25519 + Windows CSC secrets configured for tag release",
                status="missing",
                tier="public_rc",
                detail=detail,
            )
        )

    for item_id, title, detail in [
        (
            "ci_three_platforms_green",
            "Same candidate green on Linux x64, Linux ARM64, Windows x64 CI",
            "Requires GitHub Actions run on clean hosted runners",
        ),
        (
            "signed_update_manifest",
            "Published update-manifest.signed.json on GitHub Releases",
            "Created only by signed tag pipeline",
        ),
        (
            "windows_install_upgrade_uninstall",
            "Windows silent install / same-version upgrade / uninstall evidence",
            "Produced by windows-installer workflow on CI",
        ),
        (
            "opt_install_sandbox_runtime",
            "System /opt install launches without --no-sandbox and chrome-sandbox 4755",
            "Requires sudo dpkg install end verification",
        ),
    ]:
        checks.append(
            _check(
                item_id,
                title,
                status="missing",
                tier="public_rc",
                detail=detail,
            )
        )

    local = [c for c in checks if c["tier"] == "local_dev"]
    public = [c for c in checks if c["tier"] == "public_rc"]
    local_pass = sum(1 for c in local if c["status"] == "pass")
    local_fail = sum(1 for c in local if c["status"] == "fail")
    local_missing = sum(1 for c in local if c["status"] == "missing")
    public_pass = sum(1 for c in public if c["status"] == "pass")
    public_missing = sum(1 for c in public if c["status"] == "missing")
    public_fail = sum(1 for c in public if c["status"] == "fail")
    local_ready = local_fail == 0 and local_missing == 0
    # Public RC is only ready when every public item is green; that still needs
    # remote CI. Secrets may pass early once configured in the operator env.
    public_rc_ready = public_missing == 0 and public_fail == 0 and len(public) > 0

    return {
        "schema": "aetherchat-release-evidence-v1",
        "generated_at": _now(),
        "version": version,
        "summary": {
            "local_dev_total": len(local),
            "local_dev_pass": local_pass,
            "local_dev_fail": local_fail,
            "local_dev_missing": local_missing,
            "public_rc_total": len(public),
            "public_rc_pass": public_pass,
            "public_rc_missing": public_missing,
            "public_rc_fail": public_fail,
            "local_dev_ready": local_ready,
            "public_rc_ready": public_rc_ready,
            "public_1_0_go_no_go": "Go" if local_ready and public_rc_ready else "No-Go",
            "reason": (
                "Local and public RC evidence complete."
                if local_ready and public_rc_ready
                else (
                    "Local development evidence complete; public RC still requires "
                    "signed multi-platform CI artifacts and remaining public checks."
                    if local_ready
                    else "Local development evidence incomplete; see failed/missing checks."
                )
            ),
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "refresh-metadata"])
    parser.add_argument("--version", default="0.2.0")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=ROOT / "frontend" / "release" / "release-evidence.local.json",
    )
    parser.add_argument(
        "--refresh-metadata",
        action="store_true",
        help="Regenerate SBOM/build metadata for local linux-arm64 packages before check",
    )
    args = parser.parse_args()

    if args.command == "refresh-metadata":
        paths = refresh_linux_arm64_metadata(args.version)
        print("refreshed:", *paths)
        return 0

    report = evaluate_release_evidence(
        version=args.version,
        refresh_metadata=args.refresh_metadata,
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = report["summary"]
    print("Release evidence gate")
    print(f"  version: {report['version']}")
    print(
        f"  local_dev: {summary['local_dev_pass']}/{summary['local_dev_total']} pass "
        f"(fail={summary['local_dev_fail']} missing={summary['local_dev_missing']})"
    )
    print(f"  public_rc_missing: {summary['public_rc_missing']}/{summary['public_rc_total']}")
    print(f"  local_dev_ready: {summary['local_dev_ready']}")
    print(f"  public_1_0: {summary['public_1_0_go_no_go']}")
    print(f"  reason: {summary['reason']}")
    print(f"  wrote: {args.json_out}")
    for item in report["checks"]:
        if item["status"] != "pass":
            print(f"  [{item['status']}] {item['id']}: {item['detail']}")
    return 0 if summary["local_dev_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
