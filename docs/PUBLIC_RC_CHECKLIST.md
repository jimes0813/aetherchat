# AetherChat Public RC Preflight

Generated: `2026-08-12T16:17:35Z`

## Verdict

- local_dev_ready: **True**
- env secrets ready (all four): **False**
- env Ed25519 ready: **True**
- GitHub secrets ready (all four): **False**
- GitHub Ed25519 ready: **True**
- GitHub Windows CSC ready: **False**
- tag_ready (full signed RC): **False**
- public_1.0: **No-Go**

### Blockers

- Windows CSC secrets missing (WINDOWS_CSC_LINK / WINDOWS_CSC_KEY_PASSWORD) — tag release will fail preflight

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

4. **Push signed RC tag (vX.Y.Z-rc.N)**
   - `git tag v0.2.0-rc.1 && git push origin v0.2.0-rc.1`
   - Done when: release-preflight green; three platform jobs green

5. **Verify published update-manifest.signed.json**
   - `curl -fsSL …/update-manifest.signed.json -o /tmp/m.json && AETHERCHAT_RELEASE_PUBLIC_KEY_B64=… python scripts/release_security.py verify --envelope /tmp/m.json`
   - Done when: signature verified

## Notes

- Never commit private keys or certificate material.
- Tag (`v*`) builds fail fast if secrets are missing (release-preflight).
- Branch/PR builds stay unsigned by design.
