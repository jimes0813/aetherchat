#!/usr/bin/env bash
set -euo pipefail

# Prefer the Debian/system install at /opt/AetherChat (setuid chrome-sandbox).
# User-unpacked and AppImage builds cannot ship setuid chrome-sandbox; they may
# only disable Chromium sandbox when the operator explicitly opts in via
# AETHERCHAT_ALLOW_NO_SANDBOX=1 or --no-sandbox / --aether-allow-no-sandbox.

# Broken socks:// scheme breaks OpenAI httpx inside the Python backend.
unset ALL_PROXY all_proxy || true
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}localhost,127.0.0.1,::1"
export no_proxy="${no_proxy:+$no_proxy,}localhost,127.0.0.1,::1"

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/aetherchat"
USER_UNPACKED="$DATA_DIR/current/aetherchat-frontend"
USER_APPIMAGE="$DATA_DIR/AetherChat.AppImage"

fail() {
  printf 'AetherChat: %s\n' "$1" >&2
  exit 1
}

want_no_sandbox=0
filtered_args=()
for arg in "$@"; do
  case "$arg" in
    --no-sandbox|--aether-allow-no-sandbox)
      want_no_sandbox=1
      ;;
    *)
      filtered_args+=("$arg")
      ;;
  esac
done

if [[ "${AETHERCHAT_ALLOW_NO_SANDBOX:-}" == "1" ]]; then
  want_no_sandbox=1
fi

launch_with_optional_no_sandbox() {
  local bin="$1"
  shift
  if [[ "$want_no_sandbox" -eq 1 ]]; then
    printf 'AetherChat: WARNING: launching with --no-sandbox (Chromium sandbox degraded). Prefer the Debian package at /opt/AetherChat (chrome-sandbox root:root 4755). Set only via AETHERCHAT_ALLOW_NO_SANDBOX=1 or an explicit --no-sandbox/--aether-allow-no-sandbox flag.\n' >&2
    exec "$bin" --no-sandbox "$@"
  fi
  exec "$bin" "$@"
}

if [[ -x "$USER_UNPACKED" ]]; then
  # User-unpacked installs cannot provide setuid chrome-sandbox. Without an
  # explicit opt-in, attempt a sandboxed launch (may fail); do not silently
  # inject --no-sandbox.
  launch_with_optional_no_sandbox "$USER_UNPACKED" "${filtered_args[@]+"${filtered_args[@]}"}"
fi

if [[ -x "$USER_APPIMAGE" ]]; then
  launch_with_optional_no_sandbox "$USER_APPIMAGE" "${filtered_args[@]+"${filtered_args[@]}"}"
fi

readonly APP_DIR="/opt/AetherChat"
readonly APP="$APP_DIR/aetherchat-frontend"
readonly SANDBOX="$APP_DIR/chrome-sandbox"

[[ -x "$APP" ]] || fail "secure system installation is missing at $APP (install the Debian package; do not rely on user-unpacked --no-sandbox)"
[[ -f "$SANDBOX" ]] || fail "Chromium sandbox is missing at $SANDBOX"

owner_uid="$(stat -c '%u' "$SANDBOX" 2>/dev/null)" || \
  fail "cannot inspect Chromium sandbox owner"
owner_gid="$(stat -c '%g' "$SANDBOX" 2>/dev/null)" || \
  fail "cannot inspect Chromium sandbox group"
mode="$(stat -c '%a' "$SANDBOX" 2>/dev/null)" || \
  fail "cannot inspect Chromium sandbox permissions"

if [[ "$owner_uid" != "0" || "$owner_gid" != "0" || "$mode" != "4755" ]]; then
  fail "unsafe Chromium sandbox metadata (expected root:root 4755, got ${owner_uid}:${owner_gid} ${mode}); reinstall the Debian package"
fi

# System Deb path: never append --no-sandbox, even if the env/flag is set.
if [[ "$want_no_sandbox" -eq 1 ]]; then
  printf 'AetherChat: ignoring --no-sandbox opt-in for system Deb install (chrome-sandbox is present).\n' >&2
fi
exec "$APP" "${filtered_args[@]+"${filtered_args[@]}"}"
