# F1 changelog (Agent runtime / product baseline)

## 1. OS-level sandbox fallback (Linux)

- `backend/app/sandbox/bwrap_executor.py` + selector in `executor.py`
- `docs/SANDBOX.md` — fail closed when Docker and bwrap are both missing
- Env: `AETHERCHAT_SANDBOX_BACKEND=auto|docker|bwrap`

## 2. MCP config-change re-approval

- `mcp_config_fingerprint` over transport/command/args/cwd/url/auth_type
- Drift clears `allowed_tools` → `[]`, sets `reapproval_required` + reason
- `replace_launch_config()` for launch-field updates; invoke refuses until re-granted
- Counter: `mcp_reapprovals`

## 3. Computer Use capability gating

- Default **OFF** (`computer_use_policy.json` / unset env)
- `AETHERCHAT_DISABLE_COMPUTER_USE=1` hard disable
- `AETHERCHAT_COMPUTER_USE_ENABLED=1` or `PUT /desktop/computer-use/policy`
- Consent + desktop action routes reject when disabled

## 4. Observability baseline

- Electron `crashReporter` in `main.cjs` (disable: `AETHERCHAT_CRASH_REPORTER=0`; optional `AETHERCHAT_CRASH_SUBMIT_URL`)
- `runtime_metrics` counters in diagnostics snapshot (disable: `AETHERCHAT_METRICS=0`)
- WS connect/disconnect, run failures, sandbox failures, MCP reapprovals, CU rejects

## 5. Chat virtual list

- `@tanstack/react-virtual` in `UnifiedFeed.tsx` (keeps stream bubbles + approvals)

## 6. Complexity debt

- Extracted `frontend/electron/computer-consent.cjs` from `main.cjs`

## Tests

- `tests/test_sandbox_selector.py`
- `tests/test_mcp_fingerprint.py`
- `tests/test_computer_use_policy.py`
