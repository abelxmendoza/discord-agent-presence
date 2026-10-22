#!/bin/bash
# Build "Agent Presence.app" and AgentPresence-macOS.dmg. Runs on macOS.
#
#   macos/build_app.sh [out_dir]       (default: dist/)
#
# Signing is optional. With no identity the app is ad-hoc signed and macOS
# asks people to allow it once in Privacy & Security. To ship a build that
# opens with no warning, set these (an Apple Developer account is needed):
#   MACOS_SIGN_IDENTITY  "Developer ID Application: Your Name (TEAMID)"
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD   (for notarization)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
NAME="Agent Presence"
APP="$OUT/$NAME.app"
DMG="$OUT/AgentPresence-macOS.dmg"
VER="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")"
[ -n "$VER" ] || { echo "no version in pyproject.toml"; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"

echo "==> applet $VER"
osacompile -o "$APP" "$ROOT/macos/main.applescript"
RES="$APP/Contents/Resources"
cp "$ROOT/ai_presence.py" "$ROOT/LICENSE" "$ROOT/macos/install_core.sh" "$RES/"
chmod +x "$RES/install_core.sh"
echo "$VER" >"$RES/VERSION"

echo "==> psutil wheels (Apple silicon + Intel), so installs work offline"
mkdir -p "$RES/wheels"
for plat in macosx_11_0_arm64 macosx_10_9_x86_64; do
  python3 -m pip download -q --disable-pip-version-check psutil --only-binary=:all: --no-deps \
    --platform "$plat" --python-version 3.9 --implementation cp -d "$RES/wheels"
done
ls "$RES/wheels"

echo "==> icon"
ICONSET="$OUT/AppIcon.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ROOT/macos/AppIcon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z $d $d "$ROOT/macos/AppIcon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns -o "$RES/applet.icns" "$ICONSET"
rm -rf "$ICONSET"

echo "==> Info.plist"
PLIST="$APP/Contents/Info.plist"
setp() {
  /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST"
}
setp CFBundleName "$NAME"
setp CFBundleDisplayName "$NAME"
setp CFBundleIdentifier com.agentpresence.app
setp CFBundleShortVersionString "$VER"
setp CFBundleVersion "$VER"
setp LSMinimumSystemVersion 11.0
setp NSHumanReadableCopyright "MIT License · Abel Mendoza"

echo "==> sign"
if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
  codesign --force --deep --options runtime --timestamp -s "$MACOS_SIGN_IDENTITY" "$APP"
else
  codesign --force --deep -s - "$APP"
fi
codesign --verify --deep --strict "$APP"

echo "==> dmg"
STAGE="$OUT/dmg"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/$NAME.app"
hdiutil create -quiet -volname "$NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
  codesign --force --timestamp -s "$MACOS_SIGN_IDENTITY" "$DMG"
  if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
    echo "==> notarize"
    xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_APP_PASSWORD" --wait
    xcrun stapler staple "$DMG"
  fi
fi

echo "==> done"
ls -la "$OUT"
