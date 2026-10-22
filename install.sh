#!/usr/bin/env bash
# Install the systemd user service that keeps the Discord cards running.
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"
unit="$HOME/.config/systemd/user/ai-presence.service"
mkdir -p "$(dirname "$unit")"
sed -e "s|@REPO@|$repo|" -e "s|@DISPLAY@|${DISPLAY:-:0}|" "$repo/systemd/ai-presence.service" > "$unit"
python3 "$repo/ai_presence.py" --detect >/dev/null  # writes a default config if missing
systemctl --user daemon-reload
systemctl --user enable --now ai-presence
echo "Installed. Add Application IDs to ~/.config/ai-presence/config.json, then:"
echo "  systemctl --user restart ai-presence"
