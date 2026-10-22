#!/bin/bash
# Builds dist/AgentPresence-macOS.pkg, the double-click installer. Run on macOS
# from anywhere in the repo. Set INSTALLER_IDENTITY to a "Developer ID
# Installer: ..." identity in your keychain to sign it.
set -euo pipefail
cd "$(dirname "$0")/../.."
HERE=packaging/macos
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
WORK=build/macos-pkg
APP="$WORK/root/Library/Application Support/Agent Presence"
rm -rf "$WORK" && mkdir -p "$APP/wheels" "$WORK/resources" dist

cp ai_presence.py LICENSE "$HERE/uninstall.sh" "$APP/"
# psutil for both Apple Silicon and Intel, so installing needs no network.
for plat in macosx_11_0_arm64 macosx_10_9_x86_64; do
  python3 -m pip download -q psutil --only-binary=:all: --platform "$plat" \
    --python-version 3.9 --implementation cp -d "$APP/wheels"
done

pkgbuild --root "$WORK/root" --scripts "$HERE/scripts" --install-location / \
  --identifier io.github.abelxmendoza.agent-presence --version "$VERSION" \
  "$WORK/AgentPresence-component.pkg"

cp "$HERE"/resources/* "$WORK/resources/"
cp LICENSE "$WORK/resources/LICENSE.txt"
sed -i '' "s/__VERSION__/$VERSION/g" "$WORK/resources/welcome.html"
sed "s/__VERSION__/$VERSION/g" "$HERE/distribution.xml" > "$WORK/distribution.xml"

sign=()
[ -n "${INSTALLER_IDENTITY:-}" ] && sign=(--sign "$INSTALLER_IDENTITY")
productbuild --distribution "$WORK/distribution.xml" --resources "$WORK/resources" \
  --package-path "$WORK" "${sign[@]+"${sign[@]}"}" dist/AgentPresence-macOS.pkg
echo "built dist/AgentPresence-macOS.pkg ($VERSION)"
