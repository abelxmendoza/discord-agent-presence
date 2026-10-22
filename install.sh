#!/usr/bin/env bash
# One-line install:
#   curl -fsSL https://raw.githubusercontent.com/abelxmendoza/discord-agent-presence/main/install.sh | bash
set -euo pipefail
REPO_URL="https://github.com/abelxmendoza/discord-agent-presence.git"
DEST="${AGENT_PRESENCE_HOME:-$HOME/.local/share/discord-agent-presence}"
BIN="$HOME/.local/bin"

# Linux and macOS. Windows: see install.ps1.
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
OS="$(uname -s)"
if [ "$OS" = "Linux" ]; then
  command -v xprop >/dev/null || echo "note: install xprop (e.g. sudo apt install x11-utils) for window detection"
fi

# Running from a checkout? Use it. Otherwise clone/update into $DEST.
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -f "$here/ai_presence.py" ]; then
  DEST="$here"
elif [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only -q
else
  git clone -q --depth 1 "$REPO_URL" "$DEST"
fi

# macOS needs psutil; give it a private venv so system Python stays untouched.
PY=python3
if [ "$OS" = "Darwin" ]; then
  python3 -m venv "$DEST/.venv"
  "$DEST/.venv/bin/python" -m pip install -q --upgrade pip psutil
  PY="$DEST/.venv/bin/python"
fi

mkdir -p "$BIN"
cat > "$BIN/agent-presence" <<SH
#!/bin/sh
exec "$PY" "$DEST/ai_presence.py" "\$@"
SH
chmod +x "$BIN/agent-presence"

"$PY" "$DEST/ai_presence.py" setup
case ":$PATH:" in *":$BIN:"*) ;; *) echo; echo "Add $BIN to your PATH to use the agent-presence command.";; esac
