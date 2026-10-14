# AetherChat Public RC Preflight

Generated: `2026-08-19T11:19:32Z`

## Verdict

- local_dev_ready: **True**
- env secrets ready (all four): **False**
- env Ed25519 ready: **True**
- GitHub secrets ready (all four): **False**
- GitHub Ed25519 ready: **True**
- GitHub Windows CSC ready: **False**
- linux_rc_ready (Ed25519 + Linux packages): **True**
- tag_ready (full multi-platform signed): **False**
- public_1.0: **No-Go**

### Blockers

- Windows CSC secrets missing — Linux-first RC (v*-rc*) still allowed; stable tags and full multi-platform RC need WINDOWS_CSC_LINK / WINDOWS_CSC_KEY_PASSWORD
- Ready for Linux-first RC tag (v*-rc*); add Windows CSC for full multi-platform

## Required secrets

- `AETHERCHAT_RELEASE_PRIVATE_KEY_B64`: present (len=160)
- `AETHERCHAT_RELEASE_PUBLIC_KEY_B64`: present (len=152)
- `WINDOWS_CSC_LINK`: missing (len=0)
- `WINDOWS_CSC_KEY_PASSWORD`: missing (len=0)

Ed25519 pair: Ed25519 private/public pair validates

GitHub: Ed25519 secrets configured; Windows CSC secrets still missing

## Operator steps

1. **Generate Ed25519 release keypair**
   - `python scripts/release_security.py generate-keys --output-dir ~/.aetherchat-release-keys`
   - Done when: private/public Base64 files exist offline

2. **Configure four GitHub Actions secrets**
   - `gh secret set AETHERCHAT_RELEASE_PRIVATE_KEY_B64 < private.txt && gh secret set AETHERCHAT_RELEASE_PUBLIC_KEY_B64 < public.txt && gh secret set WINDOWS_CSC_LINK < cert.p12.b64 && gh secret set WINDOWS_CSC_KEY_PASSWORD --body '…'`
   - Done when: gh secret list shows all four names

3. **Refresh local release evidence**
   - `python scripts/release_evidence.py check --refresh-metadata`
   - Done when: local_dev_ready=true

4. **Push Linux-first RC tag (vX.Y.Z-rc.N) — Ed25519 only**
   - `git tag -a v0.2.0-rc.2 -m 'Linux RC' && git push origin v0.2.0-rc.2`
   - Done when: release-preflight green; linux-x64 + linux-arm64 green; prerelease published

5. **Configure Windows CSC for full multi-platform RC/stable**
   - `gh secret set WINDOWS_CSC_LINK < cert.p12.b64 && gh secret set WINDOWS_CSC_KEY_PASSWORD --body '…'`
   - Done when: gh secret list shows WINDOWS_CSC_* ; full_rc/stable path enabled

6. **Push stable tag (vX.Y.Z) — requires all four secrets**
   - `git tag -a v0.2.0 -m 'stable' && git push origin v0.2.0`
   - Done when: release-preflight green; three platform jobs green

7. **Verify published update-manifest.signed.json**
   - `curl -fsSL …/update-manifest.signed.json -o /tmp/m.json && AETHERCHAT_RELEASE_PUBLIC_KEY_B64=… python scripts/release_security.py verify --envelope /tmp/m.json`
   - Done when: signature verified

## Notes

- Never commit private keys or certificate material.
- RC tags (`v*-rc*`) need Ed25519 only; Windows CSC optional (Linux-first path).
- Stable tags (`vX.Y.Z`) require all four secrets and fail fast without CSC.
- Branch/PR builds stay unsigned by design.

## F0 public_1.0 hard gates (2026)

Stable / public_1.0 is **No-Go** until all of the following are true:

1. **Windows Authenticode**: `WINDOWS_CSC_LINK` and `WINDOWS_CSC_KEY_PASSWORD` are configured (GitHub Actions secrets). Local/CI Windows release builds must pass `scripts/check-windows-csc-env.sh` when targeting Windows.
2. **Chromium sandbox default**: user launch helpers never inject `--no-sandbox` unless `AETHERCHAT_ALLOW_NO_SANDBOX=1` or an explicit CLI flag. Prefer Deb `/opt/AetherChat` with `chrome-sandbox` `root:root` `4755`.
3. **Electron fuses**: `frontend/package.json` `build.afterPack` runs `scripts/after-pack-fuses.cjs`; `scripts/check-electron-fuses.mjs` is green in CI.
4. **Approvals**: pending approvals and session grants persist in SQLite; backend restart cancels orphaned pending with reason `backend_restart` (UI: 需重新授权).
5. **Audit integrity**: `platform_audit.jsonl` records carry `prev_hash`/`hash`; prune archives to `platform_audit.archive.jsonl` instead of silently rewriting history.

Linux-first RC tags (`v*-rc*`) may ship without Windows CSC, but must still satisfy items 2–5.

