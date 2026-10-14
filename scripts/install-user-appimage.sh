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
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APPLICATIONS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
DESKTOP_DEST="$APPLICATIONS_DIR/aetherchat.desktop"
ICON_DEST="$ICON_DIR/aetherchat.svg"
mkdir -p "$DEST_DIR" "$DEST_DIR/bin" "$APPLICATIONS_DIR" "$ICON_DIR"
cp -f "$SRC" "$DEST"
chmod +x "$DEST"
install -m 0755 "$LAUNCHER_SRC" "$LAUNCHER_DEST"
install -m 0644 "$ROOT/frontend/build/icon.svg" "$ICON_DEST"
sed "s|@EXEC@|$LAUNCHER_DEST|g" \
  "$ROOT/frontend/linux/aetherchat.desktop.in" \
  > "$DESKTOP_DEST"
chmod 0644 "$DESKTOP_DEST"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
fi
echo "Installed $SRC"
echo " -> $DEST"
echo "Launcher $LAUNCHER_DEST (prefer Deb at /opt/AetherChat; --no-sandbox only with AETHERCHAT_ALLOW_NO_SANDBOX=1 or explicit flag)"
echo "Desktop entry $DESKTOP_DEST"
echo "Desktop launcher prefers ~/.local/share/aetherchat/current over this AppImage."
