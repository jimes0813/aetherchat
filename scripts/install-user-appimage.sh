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
mkdir -p "$DEST_DIR"
cp -f "$SRC" "$DEST"
chmod +x "$DEST"
echo "Installed $SRC"
echo " -> $DEST"
echo "Desktop launcher will prefer this AppImage over /opt/AetherChat."
