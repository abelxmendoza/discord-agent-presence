#!/bin/bash
# The work behind "Agent Presence.app". The app's dialogs call this script;
# CI calls it directly. Runs as the logged-in user, never as root.
#
#   install_core.sh status              not-installed | running <ver> | stopped <ver>
#   install_core.sh install [--skip-service]
#     (or the steps one at a time: files, runtime, service)
#   install_core.sh uninstall
#   install_core.sh python-path         the Python binary the login service runs
#   install_core.sh web [--readme]      publish to your GitHub profile, then restart
#   install_core.sh link-key            this computer's link key (made if needed)
#   install_core.sh link <key>          share one card with your other computers, then restart
#
# Exit 3 from install/runtime means no usable Python 3.9+ was found.
set -euo pipefail

RES="$(cd "$(dirname "$0")" && pwd)"
DEST="${AGENT_PRESENCE_HOME:-$HOME/Library/Application Support/Agent Presence}"
BIN="$HOME/.local/bin"
LABEL="com.agentpresence"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/agent-presence-install.log"
VENV_PY="$DEST/.venv/bin/python"

mkdir -p "$(dirname "$LOG")"
exec 3>&2 2>>"$LOG"  # fd 3 keeps the caller's stderr for messages meant for the app
note() { echo "$(date '+%F %T') $*" >>"$LOG"; }

version() { cat "$RES/VERSION" 2>/dev/null || echo "?"; }

# A Python 3.9+ that won't pop up Apple's "install developer tools" prompt.
# /usr/bin/python3 is only a stub until the Command Line Tools are installed.
find_python() {
  local c
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
           /usr/bin/python3; do
    [ -x "$c" ] || continue
    if [ "$c" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then
      continue
    fi
    if "$c" -c 'import sys, venv; assert sys.version_info >= (3, 9)' >/dev/null 2>&1; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

step_files() {
  note "install files $(version) -> $DEST"
  mkdir -p "$DEST"
  cp "$RES/ai_presence.py" "$DEST/ai_presence.py"
  cp "$RES/VERSION" "$DEST/VERSION"
  [ -f "$RES/LICENSE" ] && cp "$RES/LICENSE" "$DEST/LICENSE"
  return 0
}

step_runtime() {
  if [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import psutil' >/dev/null 2>&1; then
    note "runtime ok: $("$VENV_PY" -V 2>&1)"
    return 0
  fi
  local py
  if ! py="$(find_python)"; then
    note "no Python 3.9+ found"
    echo "no-python"
    exit 3
  fi
  note "creating venv with $py"
  rm -rf "$DEST/.venv"
  "$py" -m venv "$DEST/.venv" >>"$LOG"
  # psutil wheels ship inside the app, so this works offline. Fall back to PyPI.
  "$VENV_PY" -m pip install -q --disable-pip-version-check --no-index \
      --find-links "$RES/wheels" psutil >>"$LOG" \
    || "$VENV_PY" -m pip install -q --disable-pip-version-check psutil >>"$LOG"
  "$VENV_PY" -c 'import psutil' >/dev/null
}

step_command() {
  mkdir -p "$BIN"
  cat >"$BIN/agent-presence" <<SH
#!/bin/sh
exec "$VENV_PY" "$DEST/ai_presence.py" "\$@"
SH
  chmod +x "$BIN/agent-presence"
}

step_service() {
  note "starting login service"
  "$VENV_PY" "$DEST/ai_presence.py" setup --quiet >>"$LOG"
}

# Run the tool; on failure its last lines go to the app's error dialog.
tool() {
  local out
  if ! out="$("$VENV_PY" "$DEST/ai_presence.py" "$@" 2>&1)"; then
    note "agent-presence $1 failed: $out"
    echo "$out" | tail -n 6 >&3
    return 1
  fi
  note "agent-presence $1: $out"
  echo "$out"
}

is_running() {
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1
}

case "${1:-}" in
  version)
    version ;;
  status)
    if [ ! -f "$DEST/ai_presence.py" ] || [ ! -f "$PLIST" ]; then
      echo "not-installed"
    elif is_running; then
      echo "running $(cat "$DEST/VERSION" 2>/dev/null || echo '?')"
    else
      echo "stopped $(cat "$DEST/VERSION" 2>/dev/null || echo '?')"
    fi ;;
  files)   step_files ;;
  runtime) step_runtime; step_command ;;
  service) step_service ;;
  install)
    step_files
    step_runtime
    step_command
    [ "${2:-}" = "--skip-service" ] || step_service
    note "install done" ;;
  uninstall)
    note "uninstall"
    if [ -x "$VENV_PY" ] && [ -f "$DEST/ai_presence.py" ]; then
      "$VENV_PY" "$DEST/ai_presence.py" uninstall >>"$LOG" || true
    else
      launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
      rm -f "$PLIST"
    fi
    # Only remove the command if it's ours.
    if grep -qs "$DEST" "$BIN/agent-presence"; then rm -f "$BIN/agent-presence"; fi
    rm -rf "$DEST"
    note "uninstall done" ;;
  # These run the app's own copy of the tool (an older installed one may not
  # know the options), and each is followed by a restart, which loads it.
  web)
    step_files
    tool web setup --yes ${2:+--readme} >/dev/null
    step_service ;;
  link-key)
    step_files
    tool link --print-key ;;
  link)
    [ -n "${2:-}" ] || { echo "no key given" >&3; exit 2; }
    step_files
    tool link "$2" >/dev/null
    step_service ;;
  python-path)
    "$VENV_PY" -c 'import os, sys; print(os.path.realpath(sys._base_executable if hasattr(sys, "_base_executable") else sys.executable))' ;;
  *)
    echo "usage: $0 status|install [--skip-service]|files|runtime|service|uninstall|python-path|web [--readme]|link-key|link <key>" >&2
    exit 2 ;;
esac
