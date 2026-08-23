#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd -- "$SCRIPT_DIR/../backend" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
VERSION="$(node -p "require('$SCRIPT_DIR/package.json').version")"
PYTHON_BIN="${AETHERCHAT_PYTHON:-$PROJECT_DIR/.venv-linux/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN=python3
fi

echo "Building local FastAPI backend for $(uname -m)..."
cd "$BACKEND_DIR"
"$PYTHON_BIN" -m PyInstaller --noconfirm --clean aetherchat-backend.spec

echo "Staging backend for Electron..."
cd "$SCRIPT_DIR"
rm -rf backend
cp -a "$BACKEND_DIR/dist/aetherchat-backend" backend
chmod +x backend/aetherchat-backend

if [[ ! -d node_modules ]]; then
  echo "Installing frontend dependencies..."
  npm ci
fi

echo "Building Linux AppImage and Debian package..."
case "$(uname -m)" in
  x86_64) electron_arch=x64 ;;
  aarch64|arm64) electron_arch=arm64 ;;
  *) echo "Unsupported Linux architecture: $(uname -m)" >&2; exit 1 ;;
esac
npm run dist:linux -- --"$electron_arch"
"$SCRIPT_DIR/build-deb.sh" "$electron_arch"
if [[ "$electron_arch" == "arm64" ]]; then
  unpacked_dir="$SCRIPT_DIR/release/linux-arm64-unpacked"
else
  unpacked_dir="$SCRIPT_DIR/release/linux-unpacked"
fi
"$PYTHON_BIN" "$PROJECT_DIR/scripts/check_release_budgets.py" \
  --unpacked "$unpacked_dir" \
  --backend "$SCRIPT_DIR/backend" \
  --renderer "$SCRIPT_DIR/dist" \
  --release "$SCRIPT_DIR/release" \
  --output "$PROJECT_DIR/release-meta/AetherChat-$VERSION-linux-$electron_arch-size-budget.json"
if [[ "$electron_arch" == "arm64" ]]; then
  echo "Refreshing SBOM/build metadata and checking local release evidence..."
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/release_evidence.py" check \
    --version "$VERSION" \
    --refresh-metadata
fi
echo "Done. Packages are in $SCRIPT_DIR/release."
