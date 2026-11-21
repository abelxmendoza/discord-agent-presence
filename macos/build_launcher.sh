#!/bin/bash
# Build "Agent Presence Agent.app", the launcher the login service runs on
# macOS (see launcher.c for why). Runs on macOS with the Command Line Tools.
#
#   macos/build_launcher.sh <out_dir>
#
# Signs with MACOS_SIGN_IDENTITY when set, otherwise ad-hoc. The Info.plist
# carries no app version on purpose: an unchanged launcher keeps the same
# signature from release to release, so macOS keeps its permissions.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: build_launcher.sh <out_dir>}"
APP="$OUT/Agent Presence Agent.app"
EXE="agent-presence-agent"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

# Universal when the SDK allows it, this Mac's architecture otherwise.
if ! xcrun clang -O2 -Wall -Wextra -mmacosx-version-min=11.0 -arch arm64 -arch x86_64 \
       -o "$APP/Contents/MacOS/$EXE" "$ROOT/macos/launcher.c" 2>/dev/null; then
  xcrun clang -O2 -Wall -Wextra -mmacosx-version-min=11.0 \
    -o "$APP/Contents/MacOS/$EXE" "$ROOT/macos/launcher.c"
fi

cat >"$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>$EXE</string>
  <key>CFBundleIdentifier</key><string>com.agentpresence.agent</string>
  <key>CFBundleName</key><string>Agent Presence</string>
  <key>CFBundleDisplayName</key><string>Agent Presence</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppleEventsUsageDescription</key>
  <string>Agent Presence reads which app and window are in front so your Discord card can show what you're working on.</string>
</dict>
</plist>
PLIST

if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
  codesign --force --options runtime --timestamp \
    --entitlements "$ROOT/macos/agent.entitlements" -s "$MACOS_SIGN_IDENTITY" "$APP"
else
  codesign --force --entitlements "$ROOT/macos/agent.entitlements" -s - "$APP"
fi
codesign --verify --strict "$APP"
echo "$APP"
