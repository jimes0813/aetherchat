#!/usr/bin/env bash
set -euo pipefail

# Sign the current Linux ARM64 AppImage into frontend/release/update-manifest.signed.json.
# Uses ~/.aetherchat-release-keys or env. Never prints key material. Does not publish.

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
KEY_DIR="${AETHERCHAT_RELEASE_KEYS_DIR:-$HOME/.aetherchat-release-keys}"
APPIMAGE="${1:-}"
if [[ -z "$APPIMAGE" ]]; then
  APPIMAGE="$(ls -1t "$ROOT/frontend/release"/AetherChat-*-linux-arm64.AppImage 2>/dev/null | head -1 || true)"
fi
if [[ -z "$APPIMAGE" || ! -f "$APPIMAGE" ]]; then
  echo "No Linux ARM64 AppImage found. Build first with frontend/build-linux.sh" >&2
  exit 1
fi

load_secret() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    return 0
  fi
  local file="$KEY_DIR/${name}.txt"
  if [[ -f "$file" ]]; then
    # Strip trailing newline without echoing the value.
    IFS= read -r "$name" < "$file" || true
    export "$name"
  fi
}

load_secret AETHERCHAT_RELEASE_PRIVATE_KEY_B64
load_secret AETHERCHAT_RELEASE_PUBLIC_KEY_B64
if [[ -z "${AETHERCHAT_RELEASE_PRIVATE_KEY_B64:-}" || -z "${AETHERCHAT_RELEASE_PUBLIC_KEY_B64:-}" ]]; then
  echo "Ed25519 keys missing. Generate with: python scripts/release_security.py generate-keys --output-dir ~/.aetherchat-release-keys" >&2
  exit 1
fi

VERSION="$(node -p "require('$ROOT/frontend/package.json').version")"
NAME="$(basename "$APPIMAGE")"
URL="https://github.com/jimes0813/AetherChat/releases/latest/download/${NAME}"
OUT_DIR="$ROOT/frontend/release"
MANIFEST="$OUT_DIR/update-manifest.json"
SIGNED="$OUT_DIR/update-manifest.signed.json"
PYTHON="${AETHERCHAT_PYTHON:-$ROOT/.venv-linux/bin/python}"

"$PYTHON" "$ROOT/scripts/release_security.py" manifest \
  --version "$VERSION" \
  --release "linux,arm64,$APPIMAGE,$URL" \
  --output "$MANIFEST"
"$PYTHON" "$ROOT/scripts/release_security.py" sign \
  --manifest "$MANIFEST" \
  --output "$SIGNED"
"$PYTHON" "$ROOT/scripts/release_security.py" verify --envelope "$SIGNED"
echo "Wrote $SIGNED"
echo "Local evidence only; publish still requires a v* tag and GitHub Release upload."
