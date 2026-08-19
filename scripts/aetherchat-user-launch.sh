#!/usr/bin/env bash
set -euo pipefail

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

if [[ -x "$USER_UNPACKED" ]]; then
  # Explicit degraded mode: user-unpacked builds cannot ship setuid
  # chrome-sandbox, so Chromium requires --no-sandbox. The UI must surface
  # this channel. Public installs should use /opt/AetherChat instead.
  exec "$USER_UNPACKED" --no-sandbox "$@"
fi

if [[ -x "$USER_APPIMAGE" ]]; then
  # AppImage also cannot provide setuid chrome-sandbox for a user install.
  exec "$USER_APPIMAGE" --no-sandbox "$@"
fi

readonly APP_DIR="/opt/AetherChat"
readonly APP="$APP_DIR/aetherchat-frontend"
readonly SANDBOX="$APP_DIR/chrome-sandbox"

[[ -x "$APP" ]] || fail "secure system installation is missing at $APP"
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

exec "$APP" "$@"
