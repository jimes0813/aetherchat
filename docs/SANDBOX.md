# AetherChat command sandbox

Tool `run_command` never executes on a bare host shell.

## Backends

1. **Docker** (preferred when available): `aether-sandbox` image, `cap_drop ALL`, `no-new-privileges`, read-only root, workspace bind-mount, network `none` unless `allow_net`.
2. **bubblewrap (`bwrap`)** (Linux OS-level fallback): read-only `/`, RW bind of the session workspace only, `--unshare-net` by default, `--cap-drop ALL`.

Selection via `AETHERCHAT_SANDBOX_BACKEND`:

- `auto` (default): Docker if pingable, else bwrap
- `docker` / `bwrap`: force that backend (fail closed if missing)

## Fail-closed

If neither Docker nor `bwrap` is available, the tool returns an error and increments `sandbox_failures`. There is **no** unsandboxed fallback.

Install on Debian/Ubuntu: `sudo apt install bubblewrap`.

## Network

Default deny. Callers must pass `allow_net=True` for allowlisted networked jobs (still sandboxed).

## Isolation tiers (`AETHERCHAT_SANDBOX_TIER`)

| Tier | Behavior |
|------|----------|
| *(unset)* / `auto` | Prefer Docker; else bwrap |
| `bwrap` | Force bubblewrap |
| `docker` | Force Docker |
| `strong` | Best hardened tier available today = Docker with current `cap_drop`/read-only settings. Fail closed if Docker is missing. Future: Firecracker/Kata behind the same tier flag. |

Diagnostics expose `sandbox.tier` / `sandbox.backend` / availability flags.
