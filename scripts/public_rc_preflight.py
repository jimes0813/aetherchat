#!/usr/bin/env python3
"""Public RC preflight: secrets presence, keypair match, checklist for operators.

Does not print secret values. Never claims public 1.0 Go without remote CI proof.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SECRETS = [
    "AETHERCHAT_RELEASE_PRIVATE_KEY_B64",
    "AETHERCHAT_RELEASE_PUBLIC_KEY_B64",
    "WINDOWS_CSC_LINK",
    "WINDOWS_CSC_KEY_PASSWORD",
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_release_security():
    path = ROOT / "scripts" / "release_security.py"
    spec = importlib.util.spec_from_file_location("release_security", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _secret_status(name: str) -> dict[str, Any]:
    value = os.environ.get(name) or ""
    present = bool(value.strip())
    return {
        "name": name,
        "present": present,
        "length": len(value.strip()) if present else 0,
        # never include value
    }


def _gh_secret_names() -> list[str] | None:
    """Return secret names visible via gh, or None if gh unavailable/unauthenticated."""
    try:
        proc = subprocess.run(
            ["gh", "secret", "list", "--json", "name"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return [str(item.get("name") or "") for item in rows if item.get("name")]


def evaluate_public_rc_preflight() -> dict[str, Any]:
    release_security = _load_release_security()
    env_secrets = [_secret_status(name) for name in REQUIRED_SECRETS]
    env_all = all(item["present"] for item in env_secrets)
    ed25519_names = [
        "AETHERCHAT_RELEASE_PRIVATE_KEY_B64",
        "AETHERCHAT_RELEASE_PUBLIC_KEY_B64",
    ]
    windows_names = ["WINDOWS_CSC_LINK", "WINDOWS_CSC_KEY_PASSWORD"]
    env_ed25519 = all(bool((os.environ.get(n) or "").strip()) for n in ed25519_names)
    env_windows = all(bool((os.environ.get(n) or "").strip()) for n in windows_names)
    pair_ok = False
    pair_detail = "Ed25519 pair not checked (missing env keys)"
    priv = (os.environ.get("AETHERCHAT_RELEASE_PRIVATE_KEY_B64") or "").strip()
    pub = (os.environ.get("AETHERCHAT_RELEASE_PUBLIC_KEY_B64") or "").strip()
    if priv and pub:
        try:
            release_security.validate_ed25519_pair_b64(priv, pub)
            pair_ok = True
            pair_detail = "Ed25519 private/public pair validates"
        except Exception as exc:  # noqa: BLE001
            pair_detail = f"Ed25519 pair invalid: {exc}"

    gh_names = _gh_secret_names()
    gh_status: dict[str, Any]
    if gh_names is None:
        gh_status = {
            "available": False,
            "detail": "gh CLI not available or not authenticated; cannot list GitHub secrets",
            "present": [],
            "missing": list(REQUIRED_SECRETS),
            "ed25519_ready": False,
            "windows_csc_ready": False,
        }
    else:
        present = [name for name in REQUIRED_SECRETS if name in gh_names]
        missing = [name for name in REQUIRED_SECRETS if name not in gh_names]
        gh_ed25519 = all(name in gh_names for name in ed25519_names)
        gh_windows = all(name in gh_names for name in windows_names)
        if not missing:
            detail = "all four secrets configured in GitHub"
        elif gh_ed25519 and not gh_windows:
            detail = "Ed25519 secrets configured; Windows CSC secrets still missing"
        else:
            detail = f"missing on GitHub: {', '.join(missing)}"
        gh_status = {
            "available": True,
            "detail": detail,
            "present": present,
            "missing": missing,
            "ed25519_ready": gh_ed25519,
            "windows_csc_ready": gh_windows,
        }

    # Load local evidence summary if present
    evidence_path = ROOT / "frontend" / "release" / "release-evidence.local.json"
    local_ready = False
    if evidence_path.is_file():
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            local_ready = bool(evidence.get("summary", {}).get("local_dev_ready"))
        except Exception:  # noqa: BLE001
            local_ready = False

    steps = [
        {
            "id": "generate_keys",
            "title": "Generate Ed25519 release keypair",
            "command": "python scripts/release_security.py generate-keys --output-dir ~/.aetherchat-release-keys",
            "done_hint": "private/public Base64 files exist offline",
        },
        {
            "id": "configure_github_secrets",
            "title": "Configure four GitHub Actions secrets",
            "command": (
                "gh secret set AETHERCHAT_RELEASE_PRIVATE_KEY_B64 < private.txt && "
                "gh secret set AETHERCHAT_RELEASE_PUBLIC_KEY_B64 < public.txt && "
                "gh secret set WINDOWS_CSC_LINK < cert.p12.b64 && "
                "gh secret set WINDOWS_CSC_KEY_PASSWORD --body '…'"
            ),
            "done_hint": "gh secret list shows all four names",
        },
        {
            "id": "local_evidence",
            "title": "Refresh local release evidence",
            "command": "python scripts/release_evidence.py check --refresh-metadata",
            "done_hint": "local_dev_ready=true",
        },
        {
            "id": "tag_rc",
            "title": "Push signed RC tag (vX.Y.Z-rc.N)",
            "command": "git tag v0.2.0-rc.1 && git push origin v0.2.0-rc.1",
            "done_hint": "release-preflight green; three platform jobs green",
        },
        {
            "id": "verify_manifest",
            "title": "Verify published update-manifest.signed.json",
            "command": (
                "curl -fsSL …/update-manifest.signed.json -o /tmp/m.json && "
                "AETHERCHAT_RELEASE_PUBLIC_KEY_B64=… "
                "python scripts/release_security.py verify --envelope /tmp/m.json"
            ),
            "done_hint": "signature verified",
        },
    ]

    checklist = {
        "env_secrets_ready": env_all and pair_ok,
        "env_ed25519_ready": env_ed25519 and pair_ok,
        "env_windows_csc_ready": env_windows,
        "github_secrets_ready": bool(gh_status.get("available") and not gh_status.get("missing")),
        "github_ed25519_ready": bool(gh_status.get("ed25519_ready")),
        "github_windows_csc_ready": bool(gh_status.get("windows_csc_ready")),
        "local_dev_ready": local_ready,
        "tag_ready": False,
        "public_1_0_go_no_go": "No-Go",
        "blockers": [],
    }
    # Linux-oriented RC can proceed for tag preflight with Ed25519 only if Windows
    # is allowed to fail-fast; full signed multi-platform RC still needs CSC.
    checklist["tag_ready"] = bool(
        checklist["local_dev_ready"]
        and checklist["github_ed25519_ready"]
        and checklist["github_windows_csc_ready"]
    )
    if not checklist["local_dev_ready"]:
        checklist["blockers"].append("local_dev evidence not ready — run release_evidence.py check")
    if not checklist["github_ed25519_ready"]:
        checklist["blockers"].append("GitHub Ed25519 secrets incomplete or gh unavailable")
    if not checklist["github_windows_csc_ready"]:
        checklist["blockers"].append(
            "Windows CSC secrets missing (WINDOWS_CSC_LINK / WINDOWS_CSC_KEY_PASSWORD) — tag release will fail preflight"
        )
    if not pair_ok and env_ed25519:
        checklist["blockers"].append("Ed25519 key pair failed validation")
    if checklist["tag_ready"]:
        checklist["blockers"].append(
            "Ready to cut RC tag; still need three-platform CI green + signed manifest for public 1.0"
        )
    elif not checklist["blockers"]:
        checklist["blockers"].append(
            "CI three-platform green + signed manifest publication still required for public 1.0"
        )

    return {
        "schema": "aetherchat-public-rc-preflight-v1",
        "generated_at": _now(),
        "env_secrets": env_secrets,
        "ed25519_pair": {"ok": pair_ok, "detail": pair_detail},
        "github_secrets": gh_status,
        "local_evidence_path": str(evidence_path),
        "local_dev_ready": local_ready,
        "checklist": checklist,
        "operator_steps": steps,
        "required_secret_names": list(REQUIRED_SECRETS),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AetherChat Public RC Preflight",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Verdict",
        "",
        f"- local_dev_ready: **{report['local_dev_ready']}**",
        f"- env secrets ready (all four): **{report['checklist']['env_secrets_ready']}**",
        f"- env Ed25519 ready: **{report['checklist'].get('env_ed25519_ready')}**",
        f"- GitHub secrets ready (all four): **{report['checklist']['github_secrets_ready']}**",
        f"- GitHub Ed25519 ready: **{report['checklist'].get('github_ed25519_ready')}**",
        f"- GitHub Windows CSC ready: **{report['checklist'].get('github_windows_csc_ready')}**",
        f"- tag_ready (full signed RC): **{report['checklist'].get('tag_ready')}**",
        f"- public_1.0: **{report['checklist']['public_1_0_go_no_go']}**",
        "",
        "### Blockers",
        "",
    ]
    for item in report["checklist"]["blockers"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Required secrets", ""])
    for item in report["env_secrets"]:
        mark = "present" if item["present"] else "missing"
        lines.append(f"- `{item['name']}`: {mark} (len={item['length']})")
    lines.extend(
        [
            "",
            f"Ed25519 pair: {report['ed25519_pair']['detail']}",
            "",
            f"GitHub: {report['github_secrets']['detail']}",
            "",
            "## Operator steps",
            "",
        ]
    )
    for index, step in enumerate(report["operator_steps"], start=1):
        lines.append(f"{index}. **{step['title']}**")
        lines.append(f"   - `{step['command']}`")
        lines.append(f"   - Done when: {step['done_hint']}")
        lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- Never commit private keys or certificate material.",
            "- Tag (`v*`) builds fail fast if secrets are missing (release-preflight).",
            "- Branch/PR builds stay unsigned by design.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=ROOT / "frontend" / "release" / "public-rc-preflight.json",
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=ROOT / "docs" / "PUBLIC_RC_CHECKLIST.md",
    )
    args = parser.parse_args()
    report = evaluate_public_rc_preflight()
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = render_markdown(report)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.write_text(md, encoding="utf-8")

    print("Public RC preflight")
    print(f"  local_dev_ready: {report['local_dev_ready']}")
    print(f"  env_ed25519_ready: {report['checklist'].get('env_ed25519_ready')}")
    print(f"  env_windows_csc_ready: {report['checklist'].get('env_windows_csc_ready')}")
    print(f"  github_ed25519_ready: {report['checklist'].get('github_ed25519_ready')}")
    print(f"  github_windows_csc_ready: {report['checklist'].get('github_windows_csc_ready')}")
    print(f"  github_all_four_ready: {report['checklist']['github_secrets_ready']}")
    print(f"  tag_ready: {report['checklist'].get('tag_ready')}")
    print(f"  ed25519: {report['ed25519_pair']['detail']}")
    print(f"  github: {report['github_secrets']['detail']}")
    print(f"  public_1_0: {report['checklist']['public_1_0_go_no_go']}")
    print(f"  wrote: {args.json_out}")
    print(f"  wrote: {args.md_out}")
    for blocker in report["checklist"]["blockers"]:
        print(f"  blocker: {blocker}")
    # Exit 0 always for operator tooling; blockers are informational until CI green.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
