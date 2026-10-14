# AetherChat F0–F2 delivery summary

Implemented on the box at `/workspace/aetherchat` (local commits, not pushed).

## Commits

| Phase | Hash | Subject |
|-------|------|---------|
| F0 | `e9fda77` | sandboxed launch, electron fuses, durable approvals, audit chain |
| F1 | `30524b6` | os sandbox, mcp reapproval, cu gate, metrics, virtual feed |
| F2 | `df5c63e` | design tokens, simple mode, threat model, sandbox tiers |

## What shipped

### F0 — security install & integrity
- No unconditional `--no-sandbox`; Deb/`chrome-sandbox` preferred
- `@electron/fuses` afterPack + CI checks
- Approvals + session grants in SQLite; restart → 需重新授权
- Audit hash-chain + archive-on-prune
- Windows CSC honesty / public_1.0 gates

### F1 — agent runtime baseline
- Docker **or** bubblewrap sandbox (fail closed)
- MCP config fingerprint invalidates prior tool grants
- Computer Use **default OFF** + enterprise hard-disable
- crashReporter + runtime metrics in diagnostics
- Virtualized chat feed; computer-consent extracted from `main.cjs`

### F2 — productization
- Design tokens + `ui/` primitives (Button/Banner/Dialog/Tabs)
- **Simple / Advanced** workspace mode (default simple)
- Public `docs/THREAT_MODEL.md`
- A11y F2 manual checklist path
- Sandbox tiers incl. `strong` (Docker today; Firecracker/Kata stubbed as fail-closed)

## Apply to your machine (`AetherChat-linux`)

1. Sync or cherry-pick these commits into your local `AetherChat-linux` checkout (or copy `/workspace/aetherchat`).
2. Frontend: `cd frontend && npm ci && npm run build` (and `npm run dist:linux` / Deb build as you usually do).
3. Backend: reinstall/rebuild the PyInstaller backend if you ship a packaged binary so schema v5 + sandbox code are included.
4. Prefer **Deb** install at `/opt/AetherChat` (setuid `chrome-sandbox`). Avoid default AppImage/`--no-sandbox`.
5. Linux sandbox: `sudo apt install bubblewrap` (and Docker + `aether-sandbox` image if you want `strong`/docker tier).
6. Optional env:
   - `AETHERCHAT_SANDBOX_TIER=strong|docker|bwrap`
   - `AETHERCHAT_COMPUTER_USE_ENABLED=1` only when you intend CU
   - `AETHERCHAT_DISABLE_COMPUTER_USE=1` for enterprise lock
   - `AETHERCHAT_METRICS=0` / `AETHERCHAT_CRASH_REPORTER=0` to disable telemetry/crash reporter

## Remaining manual / ops steps

- **Windows CSC**: set `WINDOWS_CSC_LINK` + `WINDOWS_CSC_KEY_PASSWORD` before public_1.0 multi-platform stable (`docs/PUBLIC_RC_CHECKLIST.md`).
- **Real electron-builder pack**: verify fuses flipped on a produced binary (`after-pack-fuses.cjs`).
- **A11y human pass**: Orca/NVDA + 200% zoom per `docs/ACCESSIBILITY_CHECKLIST.md` F2 section.
- **First-run UX**: flip Workspace mode to Advanced when you need MCP/Automations/Computer Use.

## Docs index

- `docs/F0_CHANGELOG.md`, `docs/F1_CHANGELOG.md`, `docs/F2_CHANGELOG.md`
- `docs/SANDBOX.md`, `docs/THREAT_MODEL.md`, `docs/RELEASE_SECURITY.md`
