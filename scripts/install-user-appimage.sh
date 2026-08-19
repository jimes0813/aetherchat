#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${1:-}"
if [[ -z "$SRC" ]]; then
  SRC="$(ls -1t "$ROOT/frontend/release"/AetherChat-*-linux-*.AppImage 2>/dev/null | head -1 || true)"
fi
if [[ -z "$SRC" || ! -f "$SRC" ]]; then
  echo "No AppImage found. Build first with frontend/build-linux.sh" >&2
  exit 1
fi

DEST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/aetherchat"
DEST="$DEST_DIR/AetherChat.AppImage"
LAUNCHER_SRC="$ROOT/scripts/aetherchat-user-launch.sh"
LAUNCHER_DEST="$DEST_DIR/bin/aetherchat-launch.sh"
mkdir -p "$DEST_DIR" "$DEST_DIR/bin"
cp -f "$SRC" "$DEST"
chmod +x "$DEST"
install -m 0755 "$LAUNCHER_SRC" "$LAUNCHER_DEST"
echo "Installed $SRC"
echo " -> $DEST"
echo "Launcher $LAUNCHER_DEST (user unpacked/AppImage is degraded: Chromium sandbox off)"
echo "Desktop launcher prefers ~/.local/share/aetherchat/current over this AppImage."
