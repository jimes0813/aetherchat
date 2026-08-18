#!/usr/bin/env python3
"""Reproducible release metadata, CycloneDX SBOM and Ed25519 manifest tooling."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def npm_components(lock_path: Path) -> list[dict[str, Any]]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    components: list[dict[str, Any]] = []
    for package_path, item in sorted(lock.get("packages", {}).items()):
        if not package_path or not isinstance(item, dict) or not item.get("version"):
            continue
        name = package_path.rsplit("node_modules/", 1)[-1]
        version = str(item["version"])
        component = {
            "type": "library", "name": name, "version": version,
            "bom-ref": f"pkg:npm/{name.replace('@', '%40')}@{version}",
            "purl": f"pkg:npm/{name.replace('@', '%40')}@{version}",
        }
        if item.get("integrity"):
            algorithm, encoded = str(item["integrity"]).split("-", 1)
            if algorithm.lower() == "sha512":
                component["hashes"] = [{"alg": "SHA-512", "content": base64.b64decode(encoded).hex()}]
        components.append(component)
    return components


def python_components(requirements_path: Path) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^;\s]+)")
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        name, version = match.groups()
        normalized = name.lower().replace("_", "-")
        components.append({
            "type": "library", "name": name, "version": version,
            "bom-ref": f"pkg:pypi/{normalized}@{version}", "purl": f"pkg:pypi/{normalized}@{version}",
        })
    return components


def command_sbom(args: argparse.Namespace) -> None:
    components = npm_components(Path(args.npm_lock)) + python_components(Path(args.requirements))
    components.sort(key=lambda item: item["bom-ref"])
    serial_seed = hashlib.sha256("\n".join(item["bom-ref"] for item in components).encode()).hexdigest()
    sbom = {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{serial_seed[:8]}-{serial_seed[8:12]}-{serial_seed[12:16]}-{serial_seed[16:20]}-{serial_seed[20:32]}",
        "version": 1,
        "metadata": {
            "timestamp": args.timestamp,
            "component": {"type": "application", "name": "AetherChat", "version": args.version},
            "tools": {"components": [{"type": "application", "name": "AetherChat release_security.py", "version": "1"}]},
        },
        "components": components,
    }
    write_json(Path(args.output), sbom)


def tool_version(command: list[str]) -> str:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unavailable"


def command_metadata(args: argparse.Namespace) -> None:
    artifacts = []
    for value in args.artifact:
        item = Path(value)
        if not item.is_file():
            raise SystemExit(f"Artifact does not exist: {item}")
        artifacts.append({"name": item.name, "size": item.stat().st_size, "sha256": sha256(item)})
    metadata = {
        "schema": "aetherchat-build-metadata-v1", "version": args.version,
        "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH"),
        "built_at": args.timestamp, "platform": args.platform, "arch": args.arch,
        "host": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "tools": {
            "python": platform.python_version(), "node": tool_version(["node", "--version"]),
            "npm": tool_version(["npm", "--version"]),
        },
        "artifacts": sorted(artifacts, key=lambda item: item["name"]),
    }
    write_json(Path(args.output), metadata)


def command_manifest(args: argparse.Namespace) -> None:
    releases = []
    for value in args.release:
        try:
            target_platform, arch, file_value, url = value.split(",", 3)
        except ValueError as exc:
            raise SystemExit("--release must be platform,arch,path,https-url") from exc
        artifact = Path(file_value)
        if not artifact.is_file():
            raise SystemExit(f"Artifact does not exist: {artifact}")
        if not url.startswith("https://"):
            raise SystemExit("Release URLs must use HTTPS")
        releases.append({
            "platform": target_platform, "arch": arch, "url": url,
            "size": artifact.stat().st_size, "sha256": sha256(artifact),
        })
    manifest = {
        "schema": "aetherchat-update-v1", "version": args.version,
        "published_at": args.timestamp, "releases": sorted(releases, key=lambda item: (item["platform"], item["arch"])),
    }
    write_json(Path(args.output), manifest)


def load_private_key(env_name: str) -> Ed25519PrivateKey:
    encoded = os.environ.get(env_name)
    if not encoded:
        raise SystemExit(f"Required signing key environment variable is missing: {env_name}")
    key = serialization.load_pem_private_key(base64.b64decode(encoded, validate=True), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("Release signing key must be Ed25519")
    return key


def load_public_key(env_name: str) -> Ed25519PublicKey:
    encoded = os.environ.get(env_name)
    if not encoded:
        raise SystemExit(f"Required public key environment variable is missing: {env_name}")
    key = serialization.load_pem_public_key(base64.b64decode(encoded, validate=True))
    if not isinstance(key, Ed25519PublicKey):
        raise SystemExit("Release verification key must be Ed25519")
    return key


def command_sign(args: argparse.Namespace) -> None:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    signature = load_private_key(args.key_env).sign(canonical_json(manifest))
    write_json(Path(args.output), {
        "manifest": manifest,
        "signature": {"algorithm": "Ed25519", "value": base64.b64encode(signature).decode("ascii")},
    })


def command_verify(args: argparse.Namespace) -> None:
    envelope = json.loads(Path(args.envelope).read_text(encoding="utf-8"))
    if envelope.get("signature", {}).get("algorithm") != "Ed25519":
        raise SystemExit("Signed envelope uses an unsupported algorithm")
    try:
        signature = base64.b64decode(envelope["signature"]["value"], validate=True)
        load_public_key(args.key_env).verify(signature, canonical_json(envelope["manifest"]))
    except Exception as exc:
        raise SystemExit("Release manifest signature verification failed") from exc
    print("Release manifest signature verified")


def generate_ed25519_keypair_b64() -> tuple[str, str]:
    """Return (private_b64, public_b64) PKCS8 / SubjectPublicKeyInfo PEMs as Base64."""
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return (
        base64.b64encode(private_pem).decode("ascii"),
        base64.b64encode(public_pem).decode("ascii"),
    )


def validate_ed25519_pair_b64(private_b64: str, public_b64: str) -> None:
    """Raise ValueError if the Base64 PEMs are not a matching Ed25519 pair."""
    private = serialization.load_pem_private_key(
        base64.b64decode(private_b64, validate=True),
        password=None,
    )
    public = serialization.load_pem_public_key(base64.b64decode(public_b64, validate=True))
    if not isinstance(private, Ed25519PrivateKey) or not isinstance(public, Ed25519PublicKey):
        raise ValueError("keys must be Ed25519")
    probe = b"aetherchat-release-key-check"
    public.verify(private.sign(probe), probe)
    # Also ensure provided public matches private-derived public
    derived = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    provided = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if derived != provided:
        raise ValueError("public key does not match private key")


def command_generate_keys(args: argparse.Namespace) -> None:
    private_b64, public_b64 = generate_ed25519_keypair_b64()
    validate_ed25519_pair_b64(private_b64, public_b64)
    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        priv_path = out / "AETHERCHAT_RELEASE_PRIVATE_KEY_B64.txt"
        pub_path = out / "AETHERCHAT_RELEASE_PUBLIC_KEY_B64.txt"
        priv_path.write_text(private_b64 + "\n", encoding="utf-8")
        pub_path.write_text(public_b64 + "\n", encoding="utf-8")
        with suppress(OSError):
            priv_path.chmod(0o600)
        print(f"Wrote {priv_path} (mode 600 preferred)")
        print(f"Wrote {pub_path}")
        print("Configure GitHub Secrets with these values. Do NOT commit the private key.")
        return
    print("# Paste into GitHub repository secrets (Settings → Secrets and variables → Actions)")
    print("# NEVER commit the private key or paste it into issues/PRs.")
    print()
    print(f"AETHERCHAT_RELEASE_PRIVATE_KEY_B64={private_b64}")
    print(f"AETHERCHAT_RELEASE_PUBLIC_KEY_B64={public_b64}")
    print()
    print("Also configure Windows Authenticode (electron-builder):")
    print("  WINDOWS_CSC_LINK=<certificate file or base64>")
    print("  WINDOWS_CSC_KEY_PASSWORD=<certificate password>")


def parser() -> argparse.ArgumentParser:
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    sbom = commands.add_parser("sbom")
    sbom.add_argument("--npm-lock", required=True)
    sbom.add_argument("--requirements", required=True)
    sbom.add_argument("--version", required=True)
    sbom.add_argument("--timestamp", default=now)
    sbom.add_argument("--output", required=True)
    sbom.set_defaults(run=command_sbom)
    metadata = commands.add_parser("metadata")
    metadata.add_argument("--version", required=True)
    metadata.add_argument("--platform", required=True)
    metadata.add_argument("--arch", required=True)
    metadata.add_argument("--timestamp", default=now)
    metadata.add_argument("--artifact", action="append", default=[])
    metadata.add_argument("--output", required=True)
    metadata.set_defaults(run=command_metadata)
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--version", required=True)
    manifest.add_argument("--timestamp", default=now)
    manifest.add_argument("--release", action="append", required=True)
    manifest.add_argument("--output", required=True)
    manifest.set_defaults(run=command_manifest)
    sign = commands.add_parser("sign")
    sign.add_argument("--manifest", required=True)
    sign.add_argument("--key-env", default="AETHERCHAT_RELEASE_PRIVATE_KEY_B64")
    sign.add_argument("--output", required=True)
    sign.set_defaults(run=command_sign)
    verify = commands.add_parser("verify")
    verify.add_argument("--envelope", required=True)
    verify.add_argument("--key-env", default="AETHERCHAT_RELEASE_PUBLIC_KEY_B64")
    verify.set_defaults(run=command_verify)
    generate_keys = commands.add_parser(
        "generate-keys",
        help="Generate Ed25519 release keypair as Base64 for GitHub secrets",
    )
    generate_keys.add_argument(
        "--output-dir",
        default="",
        help="If set, write key files there instead of printing to stdout",
    )
    generate_keys.set_defaults(run=command_generate_keys)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    arguments.run(arguments)
