#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(node -p "require('$SCRIPT_DIR/package.json').version")"
ELECTRON_ARCH="${1:-arm64}"

case "$ELECTRON_ARCH" in
  arm64)
    DEB_ARCH=arm64
    UNPACKED_DIR="$SCRIPT_DIR/release/linux-arm64-unpacked"
    ;;
  x64)
    DEB_ARCH=amd64
    UNPACKED_DIR="$SCRIPT_DIR/release/linux-unpacked"
    ;;
  *)
    echo "Unsupported Electron architecture: $ELECTRON_ARCH" >&2
    exit 1
    ;;
esac

if [[ ! -x "$UNPACKED_DIR/aetherchat-frontend" ]]; then
  echo "Packaged Electron directory is missing: $UNPACKED_DIR" >&2
  exit 1
fi

STAGING_DIR="$(mktemp -d "$SCRIPT_DIR/release/.aetherchat-deb.XXXXXX")"
trap 'rm -rf "$STAGING_DIR"' EXIT

install -d "$STAGING_DIR/DEBIAN" "$STAGING_DIR/opt/AetherChat"
cp -a "$UNPACKED_DIR/." "$STAGING_DIR/opt/AetherChat/"
# dpkg-deb records root ownership below; Chromium's Linux SUID sandbox also
# requires this exact mode in the installed package.
chmod 4755 "$STAGING_DIR/opt/AetherChat/chrome-sandbox"

install -d \
  "$STAGING_DIR/usr/bin" \
  "$STAGING_DIR/usr/share/applications" \
  "$STAGING_DIR/usr/share/icons/hicolor/scalable/apps"
install -m 0755 "$SCRIPT_DIR/linux/aetherchat" "$STAGING_DIR/usr/bin/aetherchat"
install -m 0644 "$SCRIPT_DIR/build/icon.svg" \
  "$STAGING_DIR/usr/share/icons/hicolor/scalable/apps/aetherchat.svg"

sed 's|@EXEC@|/usr/bin/aetherchat|g' \
  "$SCRIPT_DIR/linux/aetherchat.desktop.in" \
  > "$STAGING_DIR/usr/share/applications/aetherchat.desktop"
chmod 0644 "$STAGING_DIR/usr/share/applications/aetherchat.desktop"

INSTALLED_SIZE="$(du -sk "$STAGING_DIR/opt/AetherChat" | cut -f1)"
cat > "$STAGING_DIR/DEBIAN/control" <<EOF
Package: aetherchat
Version: $VERSION
Section: utils
Priority: optional
Architecture: $DEB_ARCH
Installed-Size: $INSTALLED_SIZE
Maintainer: AetherChat Maintainers <jimes0813@users.noreply.github.com>
Depends: libgtk-3-0, libnotify4, libnss3, libxss1, libxtst6, xdg-utils, libatspi2.0-0, libuuid1, libsecret-1-0
Homepage: https://github.com/jimes0813/AetherChat
Description: AetherChat multi-model collaboration desktop workspace
 A local Electron and FastAPI application for multi-model collaboration.
EOF
chmod 0644 "$STAGING_DIR/DEBIAN/control"

cat > "$STAGING_DIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -eu
sandbox=/opt/AetherChat/chrome-sandbox
owner="$(stat -c '%u:%g' "$sandbox")"
mode="$(stat -c '%a' "$sandbox")"
if [ "$owner" != "0:0" ] || [ "$mode" != "4755" ]; then
  echo "AetherChat post-install verification failed: chrome-sandbox is $owner $mode" >&2
  exit 1
fi
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate /usr/share/applications/aetherchat.desktop
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 0755 "$STAGING_DIR/DEBIAN/postinst"

OUTPUT="$SCRIPT_DIR/release/AetherChat-$VERSION-linux-$DEB_ARCH.deb"
dpkg-deb --root-owner-group --build "$STAGING_DIR" "$OUTPUT"
echo "Built $OUTPUT"
