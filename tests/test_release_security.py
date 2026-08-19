import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_security", PROJECT_ROOT / "scripts" / "release_security.py"
)
release_security = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(release_security)


def test_linux_desktop_entry_and_icon_match_electron_package():
    build_script = (PROJECT_ROOT / "frontend" / "build-deb.sh").read_text(
        encoding="utf-8"
    )
    assert "StartupWMClass=aetherchat" in build_script
    assert "StartupWMClass=AetherChat" not in build_script
    assert "Exec=/usr/bin/aetherchat %U" in build_script
    assert "Exec=/usr/bin/aetherchat aetherchat://chat" in build_script
    assert "Exec=/opt/AetherChat/aetherchat-frontend" not in build_script
    assert '"$SCRIPT_DIR/build/icon.svg"' in build_script
    assert "electron-linux/256x256.png" not in build_script

    launcher = (PROJECT_ROOT / "frontend" / "linux" / "aetherchat").read_text(
        encoding="utf-8"
    )
    assert 'readonly APP_DIR="/opt/AetherChat"' in launcher
    assert 'owner_uid="$(stat -c \'%u\'' in launcher
    assert 'owner_gid="$(stat -c \'%g\'' in launcher
    assert 'mode="$(stat -c \'%a\'' in launcher
    assert '"$owner_uid" != "0"' in launcher
    assert '"$owner_gid" != "0"' in launcher
    assert '"$mode" != "4755"' in launcher
    assert "--no-sandbox" not in launcher
    assert "--disable-setuid-sandbox" not in launcher


def test_user_unpacked_launcher_documents_explicit_degraded_no_sandbox():
    launcher = (PROJECT_ROOT / "scripts" / "aetherchat-user-launch.sh").read_text(
        encoding="utf-8"
    )
    assert "Explicit degraded mode" in launcher
    assert 'USER_UNPACKED="$DATA_DIR/current/aetherchat-frontend"' in launcher
    assert 'exec "$USER_UNPACKED" --no-sandbox' in launcher
    assert 'exec "$USER_APPIMAGE" --no-sandbox' in launcher
    system = launcher.split('readonly APP_DIR="/opt/AetherChat"', 1)[1]
    assert "--no-sandbox" not in system
    assert 'exec "$APP" "$@"' in system
    assert "4755" in system

    package = json.loads(
        (PROJECT_ROOT / "frontend" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    assert package["build"]["linux"]["icon"] == "icon.svg"
    assert (PROJECT_ROOT / "frontend" / "build" / "icon.svg").is_file()


def test_release_workflow_fails_fast_when_signing_secrets_are_missing():
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "release-preflight:" in workflow
    assert "needs: release-preflight" in workflow
    for secret_name in (
        "WINDOWS_CSC_LINK",
        "WINDOWS_CSC_KEY_PASSWORD",
        "AETHERCHAT_RELEASE_PRIVATE_KEY_B64",
        "AETHERCHAT_RELEASE_PUBLIC_KEY_B64",
    ):
        assert secret_name in workflow
    # Stable path still fail-fast without CSC; RC can proceed Ed25519-only.
    assert "Stable signed release is blocked; missing secret(s)" in workflow
    assert "missing Ed25519 secret(s)" in workflow
    assert "linux_rc" in workflow
    assert "fromJSON(needs.release-preflight.outputs.matrix)" in workflow
    assert "INCLUDE_WINDOWS" in workflow
    assert "Attach packages to GitHub Release" in workflow
    assert "if: ${{ !startsWith(github.ref, 'refs/tags/v') }}" in workflow
    assert "Download packages from GitHub Release" in workflow


def test_windows_installer_workflow_signs_when_csc_secret_present():
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(
        encoding="utf-8"
    )
    assert "CSC_LINK: ${{ secrets.WINDOWS_CSC_LINK }}" in workflow
    assert "AETHERCHAT_REQUIRE_CODE_SIGNING:" in workflow
    smoke = (PROJECT_ROOT / "scripts" / "smoke_windows_install.ps1").read_text(
        encoding="utf-8"
    )
    assert "Get-AuthenticodeSignature" in smoke
    assert 'AETHERCHAT_REQUIRE_CODE_SIGNING -eq "1"' in smoke


def _encoded_keys():
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
    return base64.b64encode(private_pem).decode(), base64.b64encode(public_pem).decode()


def test_release_manifest_signature_accepts_valid_and_rejects_tampering(tmp_path, monkeypatch, capsys):
    private_key, public_key = _encoded_keys()
    monkeypatch.setenv("TEST_RELEASE_PRIVATE", private_key)
    monkeypatch.setenv("TEST_RELEASE_PUBLIC", public_key)
    package = tmp_path / "AetherChat.AppImage"
    package.write_bytes(b"offline release payload")
    manifest_path = tmp_path / "manifest.json"
    envelope_path = tmp_path / "signed.json"

    release_security.command_manifest(SimpleNamespace(
        version="0.3.0", timestamp="2026-07-16T00:00:00Z",
        release=[f"linux,arm64,{package},https://updates.example.test/{package.name}"],
        output=str(manifest_path),
    ))
    release_security.command_sign(SimpleNamespace(
        manifest=str(manifest_path), output=str(envelope_path), key_env="TEST_RELEASE_PRIVATE",
    ))
    release_security.command_verify(SimpleNamespace(
        envelope=str(envelope_path), key_env="TEST_RELEASE_PUBLIC",
    ))
    assert "verified" in capsys.readouterr().out

    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["manifest"]["version"] = "9.9.9"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(SystemExit, match="verification failed"):
        release_security.command_verify(SimpleNamespace(
            envelope=str(envelope_path), key_env="TEST_RELEASE_PUBLIC",
        ))


def test_cyclonedx_sbom_and_build_metadata_include_pinned_components_and_hashes(tmp_path):
    sbom_path = tmp_path / "aetherchat.cdx.json"
    release_security.command_sbom(SimpleNamespace(
        npm_lock=str(PROJECT_ROOT / "frontend" / "package-lock.json"),
        requirements=str(PROJECT_ROOT / "backend" / "requirements.txt"),
        version="0.2.0", timestamp="2026-07-16T00:00:00Z", output=str(sbom_path),
    ))
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    refs = {item["bom-ref"] for item in sbom["components"]}
    assert "pkg:pypi/fastapi@0.139.0" in refs
    assert any(value.startswith("pkg:npm/electron@43.1.1") for value in refs)

    artifact = tmp_path / "package.deb"
    artifact.write_bytes(b"package")
    metadata_path = tmp_path / "metadata.json"
    release_security.command_metadata(SimpleNamespace(
        version="0.2.0", platform="linux", arch="arm64",
        timestamp="2026-07-16T00:00:00Z", artifact=[str(artifact)], output=str(metadata_path),
    ))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["artifacts"][0]["sha256"] == release_security.sha256(artifact)
    assert metadata["schema"] == "aetherchat-build-metadata-v1"
