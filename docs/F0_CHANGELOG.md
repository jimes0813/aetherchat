# F0 changelog (frontier security baseline)

Implemented on the box at `/workspace/aetherchat` for the F0 slice: default sandboxed launch, Electron fuses, persisted approvals, audit hash-chain, Windows CSC honesty, and CI gates.

## 1. Default launch without `--no-sandbox`

- `scripts/aetherchat-user-launch.sh` — no longer injects `--no-sandbox` unconditionally. Opt-in only via `AETHERCHAT_ALLOW_NO_SANDBOX=1` or `--no-sandbox` / `--aether-allow-no-sandbox`; prints a stderr warning when degraded. System Deb `/opt/AetherChat` never receives the flag.
- `scripts/install-user-appimage.sh` — messaging updated to prefer Deb + opt-in policy.
- `frontend/electron/install-channel.cjs` — `isChromiumSandboxDegraded` is true only when the process actually has `--no-sandbox`.
- `frontend/src/components/DegradedInstallBanner.tsx` — banner only when `chromiumSandbox === 'off'`.
- Docs: `frontend/LINUX_PACKAGING.md`, `docs/RELEASE_SECURITY.md`.

## 2. Electron `@electron/fuses`

- Direct `devDependency` `@electron/fuses`.
- `frontend/scripts/after-pack-fuses.cjs` wired as `build.afterPack` (all platforms): disables `RunAsNode`, `EnableNodeOptionsEnvironmentVariable`, `EnableNodeCliInspectArguments`; enables cookie encryption, ASAR-only load, embedded ASAR integrity.
- `scripts/check-electron-fuses.mjs` (+ `npm run check:fuses`) fails CI if wiring is missing.

## 3. Persist approvals

- Schema **v5**: `pending_approvals` + `session_grants` tables (`backend/app/services/db/schema.py`, `db/approvals.py`).
- `ApprovalService` persists pending rows and session grants; fail-closed if persist fails.
- Lifespan calls `bootstrap_from_db()`: cancels orphaned pending with reason `backend_restart` (需重新授权).
- `GET /approvals/restart-cancellations` exposes cancelled rows for the UI.

## 4. Audit hash-chain

- `append_audit` writes `prev_hash` + `hash` (SHA-256 of canonical payload).
- `prune_audit` archives overflow/aged lines to `platform_audit.archive.jsonl` and re-seals a verifiable active suffix (no silent history rewrite).
- `verify_audit_chain()` used in unit tests and diagnostics (`audit_chain` snapshot field).

## 5. Windows CSC / public_1.0 honesty

- `scripts/check-windows-csc-env.sh` — exits non-zero when building Windows without `WINDOWS_CSC_*`.
- `docs/PUBLIC_RC_CHECKLIST.md` and `frontend/WINDOWS_PACKAGING.md` gate public_1.0 on CSC.

## 6. CI gate

- `.github/workflows/release.yml` adds F0 steps: fuses check, unconditional `--no-sandbox` check, Windows CSC env check (skip when not Windows), existing pytest + vitest retained.

## Tests added/updated

- `tests/test_approval_persistence.py`
- `tests/test_audit_hash_chain.py`
- `tests/test_platform_governance.py` (prune expectations)
- `frontend/electron/install-channel.test.cjs`
- `frontend/src/components/DegradedInstallBanner.test.tsx`

## Remaining risks

- User AppImage/unpacked without opt-in may fail to start on Linux (intentional; Deb is the secure path).
- Audit prune bridge re-hashes the retained suffix; archives keep original hashes for offline review.
- Approval futures remain in-process; only metadata is durable across restart.
- `@electron/fuses` flips run at pack time — verify on a real `electron-builder` dist job before tagging.
- Windows CSC secrets still missing in this environment → public_1.0 remains No-Go for multi-platform stable.
