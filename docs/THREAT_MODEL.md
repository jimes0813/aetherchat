# AetherChat threat model (public)

Scope: the local-first Electron + FastAPI desktop agent workbench. Attackers are assumed to include **malicious models**, **malicious MCP servers**, **local unprivileged processes**, and **network adversaries** targeting update channels. Physical attacker and kernel rootkits are out of scope for mitigations claimed here.

## Assets

- Loopback API bearer token & desktop consent token
- SQLite chat/run DB, audit JSONL, vault/keyring secrets
- User workspace files and Computer Use (screen/input)
- Update manifests and installers (Ed25519 / Authenticode)

## Trust boundaries

1. **Renderer ↔ main** — `contextIsolation`, no `nodeIntegration`; preload exposes a narrow bridge.
2. **Main ↔ backend** — backend bound to `127.0.0.1`; Bearer auth; docs off by default.
3. **Backend ↔ tools** — approvals fail-closed; commands run only in Docker or bwrap; never bare host shell.
4. **Backend ↔ MCP** — per-connection allowlists; config fingerprint invalidates prior grants.
5. **Update path** — signed manifests; Windows public_1.0 requires CSC.

## Key threats & controls

### Local malware & loopback token
- **Threat:** another local process scrapes the API token and drives the agent.
- **Controls:** loopback bind; token in process env / memory; OS user boundary is the real perimeter. Treat same-user malware as largely in-scope compromise.
- **Residual:** same-user malware wins; harden OS account and disk encryption.

### `--no-sandbox` degraded Chromium path
- **Threat:** renderer escape is easier when Chromium sandbox is off.
- **Controls:** Deb install with setuid `chrome-sandbox`; user launchers only opt in via `AETHERCHAT_ALLOW_NO_SANDBOX=1` or explicit CLI; UI banner when degraded (F0).
- **Residual:** AppImage/unpacked users who opt in accept reduced isolation.

### MCP servers
- **Threat:** malicious or swapped MCP binary/URL exfiltrates data or runs unexpected tools.
- **Controls:** tool allowlists; approval prompts; config fingerprint re-approval on command/args/url/auth change (F1); secrets in OS keyring.
- **Residual:** a granted MCP tool can still do what it was allowed to do—minimize allowlists.

### Computer Use
- **Threat:** model-driven screen capture / keyboard / file write.
- **Controls:** default **OFF** (F1); enterprise `AETHERCHAT_DISABLE_COMPUTER_USE=1`; native consent broker; per-action/session grants; audit of tool runs.
- **Residual:** session grant remains powerful until quit/revoke.

### Sandbox tiers (Docker / bwrap / strong)
- **Threat:** `run_command` escapes to host.
- **Controls:** no unsandboxed fallback; Docker preferred; bwrap deny-by-default FS/net; `strong` requires Docker and fails closed otherwise (F2). See `docs/SANDBOX.md`.
- **Residual:** container/kernel bugs; bwrap is weaker than a dedicated VM.

### Audit log trust
- **Threat:** silent rewrite of `platform_audit.jsonl`.
- **Controls:** hash-chain (`prev_hash`/`hash`); prune archives to `platform_audit.archive.jsonl` (F0). Verification via `verify_audit_chain` / diagnostics.
- **Residual:** attacker with DB/file write can still delete archives; treat as tamper-*evident*, not tamper-*proof*.

### Update signing
- **Threat:** trojaned installer or update payload.
- **Controls:** Ed25519 update manifests; Windows Authenticode (`WINDOWS_CSC_*`) required for public_1.0 stable; branch builds unsigned by design.
- **Residual:** first install still requires trusting the download channel.

## Non-goals

- Multi-tenant remote SaaS isolation
- Protecting secrets from the same OS user
- Guaranteeing model output truthfulness

## Related docs

- `docs/RELEASE_SECURITY.md`, `docs/PUBLIC_RC_CHECKLIST.md`, `docs/SANDBOX.md`, `CODE_SIGNING_POLICY.md`, `PRIVACY.md`
