#!/bin/bash
# Removes Agent Presence installed from the macOS package:
#   sudo "/Library/Application Support/Agent Presence/uninstall.sh"
set -u
user="${SUDO_USER:-$(/usr/bin/stat -f%Su /dev/console)}"
APP="/Library/Application Support/Agent Presence"
if [ -n "$user" ] && [ "$user" != root ] && [ -x "$APP/venv/bin/python" ]; then
  launchctl asuser "$(id -u "$user")" sudo -u "$user" -H "$APP/venv/bin/python" "$APP/ai_presence.py" uninstall
fi
rm -f /usr/local/bin/agent-presence
rm -rf "$APP"
pkgutil --forget io.github.abelxmendoza.agent-presence >/dev/null 2>&1
echo "Agent Presence removed. Your config in ~/.config/ai-presence was left in place."
