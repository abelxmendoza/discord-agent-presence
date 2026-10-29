#!/usr/bin/env python3
"""Discord Rich Presence for AI coding tools (X11, no third-party deps).

Works like Discord's built-in game detection: "Playing Claude", "Playing
Cursor", one card per app, timer from when the app launched, gone when it
closes. Each card also lists that app's projects and rotates through live
lines: what its agents are doing, git stats, streak, model and CI.

Each app needs its own Discord application (that's what sets the name after
"Playing"). Config: ~/.config/ai-presence/config.json
"""
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

try:  # required on macOS / Windows; optional on Linux (/proc is used there)
    import psutil
except ImportError:
    psutil = None

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

HOME = Path.home()
CONFIG_PATH = HOME / ".config/ai-presence/config.json"
STATE_DIR = HOME / ".local/state/ai-presence"
DEFAULT_CONFIG = {
    # Optional: your own Application ID per app, from
    # discord.com/developers/applications (its name is what shows after
    # "Playing"). Blank = use the shared apps that ship with this tool.
    # Set an app to "off" to never show its card. A list of IDs gives the
    # card several names (e.g. ["<Coding id>", "<Vibe Coding id>"]); see
    # name_mode for how it picks.
    "client_ids": {"combined": "", "claude": "", "chatgpt": "", "grok": "", "gemini": "", "muse": "",
                   "copilot": "", "opencode": "", "aider": "",
                   "cursor": "", "windsurf": "", "kiro": "", "vscode": ""},
    "poll_seconds": 5,
    # How often the second line moves on to the next item.
    "rotate_seconds": 20,
    # A working agent counts if its transcript was written this recently...
    "agent_active_seconds": 300,
    # ...and a finished one keeps showing as "waiting for me" this long.
    "agent_waiting_seconds": 1800,
    # How much the Discord card shares: "minimal" (app and agent count only),
    # "balanced" (public repos only, no file names) or "full" (everything,
    # private repo names too). Sets the defaults for the show_* switches
    # below; set one of those yourself and it wins over the preset.
    "privacy": "balanced",
    # Project names listed here are shown as "private project", and get
    # no git/CI lines or GitHub button.
    "hide_projects": [],
    "show_project": True,
    "show_private_projects": False,  # name repos that aren't public on GitHub
    "show_files": False,      # "editing foo.py" vs just "editing"
    "show_actions": True,     # what agents are doing, and test/build results
    "show_git": True,         # branch and +/- lines (public repos unless show_private_projects)
    "show_streak": True,
    "show_model": True,
    "show_ci": True,          # public GitHub repos only
    "github_button": True,    # public GitHub repos only
    "promo_button": True,     # 2nd button: "Get Agent Presence" -> landing page
    "show_stats": True,       # "⏱ 3h 40m of agent time today · 💸 1.2M tokens", commits today
    "show_milestones": True,  # "🎉 PR #42 merged", "🏆 100th commit this month"
    "milestone_seconds": 600, # how long a milestone stays on the card
    "mood_verbs": True,       # Watching / Competing in, instead of always Playing
    # A line naming this computer and its OS ("💻 MacBook Pro · macOS 15"), and
    # with several linked computers, which ones are in use.
    "show_machine": True,
    "machine_name": "",       # what to call this computer; blank = its model
    # Same key on each of your computers = they share one card that follows
    # whichever one you're using. Set with `agent-presence link`.
    "link_key": "",
    # Publish what you're doing to GitHub for a README card and a website
    # widget, with more detail than Discord shows. Set up with
    # `agent-presence web setup`.
    # Private by default: only public GitHub repos are named, OS shows its
    # major version, times are rounded to the day. `share` picks which
    # highlights from your private habit log go on it. With a link key, `relay`
    # also shares your card with linked computers on other networks through
    # the repo (encrypted with the link key); only one computer publishes.
    "web": {"enabled": False, "repo": "", "branch": "agent-presence-status", "name": "",
            "relay": True,
            "live": True, "show_tokens": False, "show_files": False, "show_machines": True,
            "share": ["streak", "best_streak", "week_hours", "week_commits", "month_commits",
                      "active_days", "top_project", "tests"],
            "every_seconds": 120, "token": ""},
    # Big picture switches to <app>_failed / <app>_success after a test or
    # build run, for this long.
    "failed_mood_seconds": 900,
    "success_mood_seconds": 180,
    # This many busy agents on one app switches the big picture to <app>_crew.
    "crew_size": 3,
    # With several IDs per app: "vibe" = 1st name while you're the one coding,
    # 2nd while agents do the work; "rotate" = cycle every name_rotate_seconds.
    "name_mode": "vibe",
    "name_rotate_seconds": 600,
    # Images: "url" = PNGs at image_base_url (missing ones are skipped),
    # "assets" = same names uploaded to each Discord app's Art Assets,
    # "none" = no images.
    "images": "url",
    "image_base_url": "https://raw.githubusercontent.com/abelxmendoza/discord-agent-presence/main/images",
}

# What each privacy preset sets. DEFAULT_CONFIG holds the "balanced" values.
PRIVACY_PRESETS = {
    "minimal": {"show_project": False, "show_private_projects": False, "show_files": False,
                "show_actions": False, "show_git": False, "show_stats": False,
                "show_milestones": False, "show_streak": False, "show_model": False,
                "show_ci": False, "github_button": False, "show_machine": False},
    "balanced": {"show_project": True, "show_private_projects": False, "show_files": False,
                 "show_actions": True, "show_git": True, "show_stats": True,
                 "show_milestones": True, "show_streak": True, "show_model": True,
                 "show_ci": True, "github_button": True, "show_machine": True},
    "full": {"show_project": True, "show_private_projects": True, "show_files": True,
             "show_actions": True, "show_git": True, "show_stats": True,
             "show_milestones": True, "show_streak": True, "show_model": True,
             "show_ci": True, "github_button": True, "show_machine": True},
}
# Before presets, every switch was written to config.json at its default, so
# these values there mean "never chosen" and now follow the preset.
_OLD_DEFAULTS = {"show_project": True, "show_files": True, "show_git": True, "show_stats": True,
                 "show_milestones": True, "show_ci": True, "github_button": True,
                 "show_machine": True, "show_streak": True, "show_model": True}

# Shared Discord applications, so the cards work with zero setup.
# Application IDs are public identifiers, not secrets.
SHARED_CLIENT_IDS = {
    # One card for every tool that has no Discord app of its own: "Coding"
    # while you type, "Vibe Coding" while agents work (name_mode). The big
    # picture and lines follow whichever tool you're using.
    "combined": ["1562634034390114445", "1562654272749965452"],
    "claude": "",
    "chatgpt": "",
    "grok": "",
    "gemini": "",
    "muse": "",
    "copilot": "",
    "opencode": "",
    "aider": "",
    "cursor": "",
    "windsurf": "",
    "kiro": "",
    "vscode": "",
}

LANDING_URL = "https://abelxmendoza.github.io/discord-agent-presence/"

TOOLS = ["claude", "chatgpt", "grok", "gemini", "muse", "copilot", "opencode", "aider",
         "cursor", "windsurf", "kiro", "vscode"]
EDITORS = {"cursor", "windsurf", "kiro", "vscode"}
TOOL_NAMES = {"claude": "Claude", "chatgpt": "ChatGPT · Codex", "grok": "Grok Build",
              "gemini": "Gemini", "muse": "Muse Code", "copilot": "GitHub Copilot",
              "opencode": "OpenCode", "aider": "Aider", "windsurf": "Windsurf", "kiro": "Kiro",
              "cursor": "Cursor", "vscode": "VS Code", "combined": "Combined card"}
# Desktop app main processes -> tool. Process names are compared lowercased
# with any ".exe" removed.
APP_PROCS = {"claude-desktop": "claude", "chatgpt": "chatgpt", "grok-bot": "grok",
             "windsurf": "windsurf", "kiro": "kiro", "cursor": "cursor", "code": "vscode"}
# macOS: the app bundle a main executable lives in -> tool
MAC_BUNDLES = {"claude.app": "claude", "chatgpt.app": "chatgpt", "codex.app": "chatgpt",
               "cursor.app": "cursor", "visual studio code.app": "vscode",
               "windsurf.app": "windsurf", "kiro.app": "kiro", "grok.app": "grok"}
# macOS front-app names (System Events) -> tool
MAC_APP_NAMES = {"claude": "claude", "chatgpt": "chatgpt", "codex": "chatgpt", "cursor": "cursor",
                 "code": "vscode", "visual studio code": "vscode", "windsurf": "windsurf",
                 "kiro": "kiro", "grok": "grok"}
# CLI agent process names -> tool
CLI_AGENTS = {"claude": "claude", "codex": "chatgpt", "grok": "grok", "muse": "muse",
              "opencode": "opencode", ".opencode": "opencode", "aider": "aider",
              "gemini": "gemini", "copilot": "copilot"}
# CLI agents with no session log we can read: a running process is the agent.
PROC_AGENTS = {"grok", "muse", "gemini", "copilot", "opencode", "aider"}
# Agents that run under a JS runtime, so /proc comm is just "node": match the
# command line instead.
# (Command lines are \x00-separated; paths may use / or \\.)
CMDLINE_AGENTS = [
    (re.compile(r"(?:^|[/\\])gemini(?:\.js|\.cmd|\.exe)?(?:\x00|$)|@google[/\\]gemini-cli"), "gemini"),
    (re.compile(r"(?:^|[/\\])copilot(?:\.js|\.cmd|\.exe)?(?:\x00|$)|@github[/\\]copilot"), "copilot"),
    (re.compile(r"(?:^|[/\\])opencode(?:\.js|\.cmd|\.exe)?(?:\x00|$)|opencode-ai"), "opencode"),
    # aider is Python; usually the process is "aider", but `python -m aider` isn't
    (re.compile(r"(?:^|[/\\])aider(?:\.exe)?(?:\x00|$)|\x00-m\x00aider(?:\x00|$)"), "aider")]
JS_RUNTIMES = {"node", "bun", "deno", "python", "python3"}
# WM_CLASS (lowercased, snap suffix stripped) -> tool
WINDOW_APPS = {"com.anthropic.claude": "claude", "chatgpt": "chatgpt", "grok-bot": "grok",
               "windsurf": "windsurf", "kiro": "kiro",
               "cursor": "cursor", "code": "vscode"}
# Claude Code transcript "entrypoint" -> tool that hosts the agent
CLAUDE_ENTRYPOINTS = {"claude-vscode": "vscode", "claude-cursor": "cursor"}
TERMINALS = {"gnome-terminal-server", "gnome-terminal", "kitty", "alacritty",
             "konsole", "xterm", "wezterm", "tilix", "terminator", "ghostty",
             # macOS
             "terminal", "iterm2", "warp", "wezterm-gui", "hyper", "tabby",
             # Windows
             "windowsterminal", "cmd", "powershell", "pwsh", "conhost", "openconsole", "mintty"}
# Tools that mean the agent has stopped to ask you something.
ASKING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}

SEP = " ✦ "
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
# Discord activity types
PLAYING, WATCHING, COMPETING = 0, 3, 5


def log(*a):
    line = " ".join([time.strftime("%H:%M:%S"), *map(str, a)])
    if sys.stdout is not None:  # None under pythonw on Windows
        try:
            print(line, flush=True)
            return
        except (OSError, ValueError):
            pass
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        f = STATE_DIR / "agent-presence.log"
        if f.exists() and f.stat().st_size > 1_000_000:
            f.replace(f.with_suffix(".log.1"))
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def client_ids(cfg):
    """App -> list of Application IDs: user's own, else the shared ones;
    "off" disables the card."""
    ids = {}
    for tool in ["combined", *TOOLS]:
        mine = cfg["client_ids"].get(tool) or ""
        if isinstance(mine, str) and mine.strip().lower() == "off":
            continue
        chosen = mine or SHARED_CLIENT_IDS.get(tool) or []
        chosen = [chosen] if isinstance(chosen, str) else list(chosen)
        chosen = [c.strip() for c in chosen if c and c.strip()]
        if chosen:
            ids[tool] = chosen
    return ids


def pick_name(cfg, s, n):
    """Index of which Discord app (name) should show this card right now."""
    if n < 2 or not s:
        return 0
    if cfg["name_mode"] == "rotate":
        return int(time.time() // cfg["name_rotate_seconds"]) % n
    agents_working = any(a["status"] == "busy" for a in s["agents"])
    return 1 if agents_working else 0


def preset(cfg):
    p = cfg.get("privacy")
    return PRIVACY_PRESETS[p if p in PRIVACY_PRESETS else "balanced"]


def private_dir(path):
    """Make a folder only this user can read (it holds a token and your habits)."""
    path.mkdir(parents=True, exist_ok=True)
    if not IS_WIN:  # Windows profile folders are already per-user
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def save_config(cfg):
    """Write config.json, owner-only. Switches left at their preset's value
    aren't written, so changing "privacy" later still moves them."""
    out = {k: v for k, v in cfg.items() if not (k in preset(cfg) and v == preset(cfg)[k])}
    private_dir(CONFIG_PATH.parent)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(out, indent=2) + "\n")
    if not IS_WIN:
        os.chmod(tmp, 0o600)  # in case it already existed with looser permissions
    os.replace(tmp, CONFIG_PATH)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    user = {}
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text())
        for old in ("client_id", "linger_seconds", "include_vscode", "use_images"):
            user.pop(old, None)  # from earlier versions
        if "privacy" not in user:  # from before presets
            for k, v in _OLD_DEFAULTS.items():
                if user.get(k) == v:
                    user.pop(k)
    cfg["privacy"] = user.get("privacy", cfg["privacy"])
    cfg.update(preset(cfg))
    cfg.update(user)
    cfg["client_ids"] = {**DEFAULT_CONFIG["client_ids"], **user.get("client_ids", {})}
    cfg["web"] = {**DEFAULT_CONFIG["web"], **user.get("web", {})}
    save_config(cfg)
    private_dir(STATE_DIR)
    return cfg


def cached(store, key, ttl, fn):
    hit = store.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    store[key] = (time.time(), val)
    return val


def run(*cmd, cwd=None, timeout=5):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


# ------------------------------------------------------------- windows ----
# Every backend returns windows as {"tool", "terminal", "pid", "title"}.

def norm_name(name):
    name = (name or "").lower()
    return name[:-4] if name.endswith(".exe") else name


def _x11_window(wid):
    info = run("xprop", "-id", wid, "WM_CLASS", "_NET_WM_PID", "_NET_WM_NAME")
    cls, pid, title = [], None, ""
    for line in info.splitlines():
        if line.startswith("WM_CLASS"):
            # Snap apps report "code_code", "firefox_firefox", ...
            cls = [c.strip().strip('"').lower().split("_")[0]
                   for c in line.split("=", 1)[1].split(",")]
        elif line.startswith("_NET_WM_PID"):
            try:
                pid = int(line.split("=", 1)[1])
            except ValueError:
                pass
        elif line.startswith("_NET_WM_NAME"):
            title = line.split("=", 1)[1].strip().strip('"')
    tool = next((t for key, t in WINDOW_APPS.items() if key in cls), None)
    return {"tool": tool, "terminal": bool(set(cls) & TERMINALS), "pid": pid, "title": title}


def _x11_windows():
    out = run("xprop", "-root", "_NET_ACTIVE_WINDOW")
    wid = out.split()[-1] if out else ""
    active = _x11_window(wid) if wid.startswith("0x") and wid != "0x0" else None
    out = run("xprop", "-root", "_NET_CLIENT_LIST")
    return active, [_x11_window(w.strip(",")) for w in out.split() if w.startswith("0x")]


MAC_SCRIPT = """
set out to ""
tell application "System Events"
  set fp to first application process whose frontmost is true
  set out to "FRONT" & tab & (name of fp) & tab & (unix id of fp) & tab
  try
    set out to out & (name of front window of fp)
  end try
  set out to out & linefeed
  repeat with pn in {"Cursor", "Code", "Windsurf", "Kiro"}
    set pn to contents of pn
    if exists process pn then
      try
        repeat with w in (windows of process pn)
          set out to out & "WIN" & tab & pn & tab & (name of w) & linefeed
        end repeat
      end try
    end if
  end repeat
end tell
return out
"""


def _mac_windows():
    """Front app via System Events. Window titles need the Accessibility
    permission; without it we still get the front app, just no titles."""
    active, wins = None, []
    for line in run("osascript", "-e", MAC_SCRIPT).splitlines():
        f = line.split("\t")
        if f[0] == "FRONT" and len(f) >= 3:
            name = f[1].lower()
            active = {"tool": MAC_APP_NAMES.get(name), "terminal": name in TERMINALS,
                      "pid": int(f[2]) if f[2].isdigit() else None,
                      "title": f[3] if len(f) > 3 else ""}
        elif f[0] == "WIN" and len(f) >= 3:
            wins.append({"tool": MAC_APP_NAMES.get(f[1].lower()), "terminal": False,
                         "pid": None, "title": f[2]})
    return active, wins


def _win_windows(procs):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    def info(hwnd):
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name, _, _, exe = procs.get(pid.value, ("", 0, 0, ""))
        return {"tool": app_tool(name, exe), "terminal": name in TERMINALS,
                "pid": pid.value, "title": buf.value}

    wins = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            wins.append(info(hwnd))
        return True
    user32.EnumWindows(proto(each), 0)
    fg = user32.GetForegroundWindow()
    return (info(fg) if fg else None), wins


def desktop_windows(procs):
    """(focused window or None, all windows) on this platform."""
    try:
        if IS_WIN:
            return _win_windows(procs)
        if IS_MAC:
            return _mac_windows()
        return _x11_windows()
    except Exception:
        return None, []


# ------------------------------------------------------------ processes ----

def _proc_table_linux():
    boot = time.time() - float(Path("/proc/uptime").read_text().split()[0])
    procs = {}
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            comm = stat[stat.index("(") + 1:stat.rindex(")")]
            rest = stat.rsplit(")", 1)[1].split()
            ppid, start = int(rest[1]), boot + int(rest[19]) / CLK_TCK
        except (OSError, ValueError, IndexError):
            continue
        try:
            exe = os.readlink(d / "exe")
        except OSError:
            exe = ""
        procs[int(d.name)] = (norm_name(comm), ppid, start, exe)
    return procs


def _proc_table_psutil():
    procs = {}
    for p in psutil.process_iter(["pid", "name", "ppid", "create_time", "exe"]):
        i = p.info
        procs[i["pid"]] = (norm_name(i["name"]), i["ppid"] or 0, i["create_time"] or 0.0,
                           i["exe"] or "")
    return procs


def proc_table():
    """{pid: (name, ppid, start_epoch, exe)} plus a children map."""
    if IS_LINUX and Path("/proc/uptime").exists():
        procs = _proc_table_linux()
    elif psutil:
        procs = _proc_table_psutil()
    else:
        procs = {}
    kids = {}
    for pid, (_, ppid, _, _) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    return procs, kids


def descendants(kids, root):
    stack, out = [root], []
    while stack:
        for c in kids.get(stack.pop(), []):
            out.append(c)
            stack.append(c)
    return out


def proc_cwd(pid):
    try:
        if IS_LINUX:
            return os.readlink(f"/proc/{pid}/cwd")
        return psutil.Process(pid).cwd() if psutil else None
    except Exception:
        return None


def app_tool(name, exe):
    """Which desktop app a process is the main process of, if any."""
    exe = exe or ""
    if IS_MAC:
        m = re.search(r"/([^/]+\.app)/Contents/MacOS/[^/]+$", exe)
        return MAC_BUNDLES.get(m.group(1).lower()) if m else None
    if IS_WIN and name == "claude":
        # Desktop app lives in %LOCALAPPDATA%\AnthropicClaude; the CLI elsewhere.
        return "claude" if "anthropicclaude" in exe.lower() else None
    return APP_PROCS.get(name)


# ------------------------------------------------------------ projects ----

KNOWN_ROOTS = {}  # project name -> repo root, learned from agent cwds


def resolve_project(cwd):
    """cwd -> (project name, repo root) or (None, None)."""
    if not cwd:
        return None, None
    p = Path(cwd)
    # Desktop "chat" scratch dirs aren't projects.
    parts = [x.lower() for x in p.parts]
    if "scratch-workspaces" in parts or any(
            a == "documents" and b == "codex" for a, b in zip(parts, parts[1:])):
        return None, None
    if p == HOME or any(p == h for _, h in wsl_homes()):
        return None, None
    root = p
    for parent in [p, *p.parents]:  # prefer the git repo root
        if (parent / ".git").exists():
            root = parent
            break
        if parent == HOME:
            break
    KNOWN_ROOTS[root.name] = str(root)
    return root.name, str(root)


def root_for_name(name):
    """Find a repo for a project name seen only in an editor title."""
    if name in KNOWN_ROOTS:
        return KNOWN_ROOTS[name]
    candidates = [HOME / d / name for d in ("Desktop", "", "Documents", "projects", "code", "src",
                                            "dev", "Developer", "git", "GitHub", "source/repos")]
    candidates += list((HOME / "Desktop").glob(f"*/{name}"))
    for c in candidates:
        if (c / ".git").exists():
            KNOWN_ROOTS[name] = str(c)
            return str(c)
    return None


EDITOR_TITLE_SUFFIXES = {"visual studio code", "cursor", "windsurf", "kiro", "code - oss",
                         "vscodium", "code"}


def editor_project(title):
    """Project from an editor window title. Linux/Windows end with the app name
    ("file.py - project - Cursor"); macOS leaves it off ("file.py — project")."""
    parts = [p.strip().lstrip("●").strip() for p in re.split(r"\s[—–-]\s", title or "")]
    parts = [p for p in parts if p]
    if parts and parts[-1].lower() in EDITOR_TITLE_SUFFIXES:
        parts.pop()
    if not parts:
        return None
    name = parts[-1]
    if name.startswith("[") and "]" in name:  # "[SSH: host] project"
        name = name.split("]", 1)[1].strip() or name
    return None if name.lower() in ("welcome", "untitled (workspace)", "get started") else name


# -------------------------------------------------------------- agents ----

def tail_records(path, nbytes=262144):
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - nbytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def pretty_model(m):
    if not m:
        return None
    m = re.sub(r"-\d{8}$", "", m)                    # drop date suffixes
    m = re.sub(r"^claude-", "", m)
    m = re.sub(r"(\d+)-(\d+)", r"\1.\2", m)          # opus-5-5 -> opus-5.5
    parts = m.replace("-", " ").split()
    nice = []
    for p in parts:
        if p.lower() == "gpt":
            nice.append("GPT")
        elif re.match(r"^\d", p):
            nice.append(p)
        else:
            nice.append(p.capitalize())
    s = " ".join(nice)
    return s.replace("GPT ", "GPT-")


# File names never shown, even with show_files on.
SENSITIVE_FILE = re.compile(
    r"^\.env|^\.npmrc$|^\.netrc$|^\.pypirc$|^id_(rsa|dsa|ecdsa|ed25519)|secret|passw|credential|"
    r"token|private|api[_-]?key|(^|[._-])keys?([._-]|$)|\.(pem|key|p12|pfx|kdbx|keystore|jks|ovpn)$",
    re.I)


def action_for(tool, args, show_files):
    """Turn an agent's latest tool call into a short, friendly line."""
    args = args if isinstance(args, dict) else {}
    fname = Path(str(args.get("file_path") or args.get("notebook_path") or "")).name
    if SENSITIVE_FILE.search(fname):
        fname = ""
    f = f" {fname}" if show_files and fname else ""
    t = tool.lower()
    cmd = args.get("command") or args.get("cmd") or ""
    if isinstance(cmd, list):
        cmd = " ".join(map(str, cmd))
    cmd = str(cmd)
    if t in ("replace", "write_file", "edit_file"):
        t = "edit"
    elif t in ("read_file", "read_many_files"):
        t = "read"
    elif t in ("glob", "search_file_content", "list_directory", "grep_search"):
        t = "grep"
    elif t in ("google_web_search", "web_fetch"):
        t = "websearch"
    elif t == "run_shell_command":
        t = "shell"
    if t in ("edit", "write", "multiedit", "notebookedit", "apply_patch"):
        return f"✍ editing{f or ' files'}"
    if t == "read":
        return f"📖 reading{f or ' code'}"
    if t in ("grep", "glob", "search", "find"):
        return "🔍 searching the codebase"
    if t in ("websearch", "webfetch", "web_search"):
        return "🌐 researching"
    if t in ("agent", "task"):
        return "🤖 directing a subagent"
    if "browser" in t or "preview" in t:
        return "🖥 testing in the browser"
    if t in ("bash", "shell", "exec_command", "local_shell", "run"):
        if re.search(r"\b(pytest|unittest|colcon test|ctest|cargo test|go test|npm (run )?test|vitest|jest)\b", cmd):
            return "🧪 running tests"
        if re.search(r"\b(colcon build|cmake|make|ninja|cargo build|npm run build|go build|docker build|pip install)\b", cmd):
            return "🔨 building"
        if re.search(r"\bgit (commit)\b", cmd):
            return "📦 committing"
        if re.search(r"\bgit (push)\b", cmd):
            return "🚀 pushing"
        if re.search(r"\b(ros2|gz|px4|mavlink|roslaunch)\b", cmd):
            return "🛸 running the sim"
        return "⚙ running commands"
    return "⚡ working"


OUTCOME_KINDS = {"🧪": "tests", "🔨": "build", "🚀": "push"}


def iso_time(ts):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def make_outcome(action, ok, when):
    """Result of the newest test/build run: {"kind", "ok", "t"} or None."""
    kind = OUTCOME_KINDS.get(action.split()[0])
    return {"kind": kind, "ok": ok, "t": when} if kind and when else None


def claude_agent(f, mtime, cfg):
    recs = tail_records(f)
    info = {"tool": "claude", "mtime": mtime, "model": None, "cwd": None,
            "branch": None, "status": "busy", "action": "💭 thinking"}
    for r in reversed(recs):
        if not info["cwd"] and r.get("cwd"):
            info["cwd"] = r["cwd"]
            info["tool"] = CLAUDE_ENTRYPOINTS.get(r.get("entrypoint"), "claude")
            info["branch"] = r.get("gitBranch")
        msg = r.get("message") or {}
        if not info["model"] and r.get("type") == "assistant" and msg.get("model", "").startswith("claude"):
            info["model"] = msg["model"]
        if info["cwd"] and info["model"]:
            break
    convo = [r for r in recs if r.get("type") in ("user", "assistant") and not r.get("isSidechain")]
    age = time.time() - mtime
    if convo:
        last = convo[-1]
        msg = last.get("message") or {}
        content = msg.get("content")
        if last["type"] == "assistant":
            uses = [c for c in content if c.get("type") == "tool_use"] if isinstance(content, list) else []
            if uses:
                use = uses[-1]
                if use.get("name") in ASKING_TOOLS:
                    info["status"], info["action"] = "waiting", "⏳ waiting for me"
                elif age > 180 and use.get("name") not in ("Bash", "Agent", "Task"):
                    # Stuck on a tool call for minutes: it's asking permission.
                    info["status"], info["action"] = "waiting", "⏳ needs my approval"
                else:
                    info["action"] = action_for(use.get("name", ""), use.get("input"), cfg["show_files"])
                    # A build, test run or subagent can go quiet for many minutes.
                    info["long_running"] = use.get("name") in ("Bash", "Agent", "Task")
            elif msg.get("stop_reason") == "end_turn":
                info["status"], info["action"] = "waiting", "⏳ waiting for me"

    # Did the newest test/build run pass? Bash results are flagged is_error
    # on a non-zero exit code.
    uses, info["outcome"] = {}, None
    for r in convo:
        content = (r.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if c.get("type") == "tool_use":
                uses[c.get("id")] = action_for(c.get("name", ""), c.get("input"), False)
            elif c.get("type") == "tool_result" and c.get("tool_use_id") in uses:
                o = make_outcome(uses[c["tool_use_id"]], not c.get("is_error"), iso_time(r.get("timestamp")))
                if o:
                    info["outcome"] = o
    return info


def codex_agent(f, mtime, cfg):
    info = {"tool": "chatgpt", "mtime": mtime, "model": None, "cwd": None,
            "branch": None, "status": "busy", "action": "💭 thinking"}
    try:
        with open(f) as fh:
            meta = json.loads(fh.readline()).get("payload", {})
        info["cwd"] = meta.get("cwd")
        info["branch"] = (meta.get("git") or {}).get("branch")
    except (OSError, json.JSONDecodeError):
        pass
    recs = tail_records(f)
    for r in reversed(recs):
        p = r.get("payload") or {}
        if r.get("type") == "turn_context" and p.get("model"):
            info["model"] = p["model"]
            break
    for r in reversed(recs):
        p = r.get("payload") or {}
        kind = p.get("type")
        if kind in ("task_complete", "turn_aborted"):
            info["status"], info["action"] = "waiting", "⏳ waiting for me"
            break
        if kind in ("function_call", "custom_tool_call", "local_shell_call"):
            args = p.get("arguments") or p.get("input") or p.get("action") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"command": args}
            info["action"] = action_for(p.get("name") or kind, args, cfg["show_files"])
            break
        if kind == "message" and p.get("role") == "user":
            break

    calls, info["outcome"] = {}, None
    for r in recs:
        p = r.get("payload") or {}
        kind = p.get("type")
        if kind in ("function_call", "custom_tool_call", "local_shell_call"):
            args = p.get("arguments") or p.get("input") or p.get("action") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"command": args}
            calls[p.get("call_id")] = action_for(p.get("name") or kind, args, False)
        elif kind in ("function_call_output", "custom_tool_call_output") and p.get("call_id") in calls:
            m = re.search(r'(?:exit_code\\?"?\s*[:=]|exited with code)\s*(-?\d+)',
                          json.dumps(p.get("output")), re.I)
            if m:
                o = make_outcome(calls[p["call_id"]], m.group(1) == "0", iso_time(r.get("timestamp")))
                if o:
                    info["outcome"] = o
    return info


def proc_cmdline(pid):
    """Command line, \\x00-separated like /proc/<pid>/cmdline."""
    try:
        if IS_LINUX:
            return Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
        return "\x00".join(psutil.Process(pid).cmdline()) + "\x00" if psutil else ""
    except Exception:
        return ""


def agent_tool_for(pid, comm, exe=""):
    """Which agent CLI a process is, if any (by name, or command line for
    runtimes like node / python)."""
    if app_tool(comm, exe):
        return None  # e.g. the Claude desktop app, not the claude CLI
    if comm in CLI_AGENTS:
        return CLI_AGENTS[comm]
    if comm in JS_RUNTIMES or comm.startswith("python"):
        cmd = proc_cmdline(pid)
        for pattern, tool in CMDLINE_AGENTS:
            if pattern.search(cmd):
                return tool
    return None


CPU_SEEN = {}  # pid -> (cpu ticks, wall time) from the previous poll


def cpu_seconds(pid):
    try:
        if IS_LINUX:
            f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return (int(f[11]) + int(f[12])) / CLK_TCK
        t = psutil.Process(pid).cpu_times()
        return t.user + t.system
    except Exception:
        return None


def cpu_busy(pid):
    """True if the process used a noticeable amount of CPU since the last poll."""
    secs = cpu_seconds(pid)
    if secs is None:
        return False
    now = time.time()
    prev = CPU_SEEN.get(pid)
    CPU_SEEN[pid] = (secs, now)
    if not prev:
        return True  # first sighting: give it the benefit of the doubt
    rate = (secs - prev[0]) / max(now - prev[1], 0.1)
    return rate > 0.02  # >2% of a core


def activity_mtime(tool, cwd):
    """Newest write to a file this CLI updates while it works (0 if none)."""
    paths = []
    if tool == "aider" and cwd:
        root = resolve_project(cwd)[1] or cwd
        paths = [Path(root) / ".aider.chat.history.md", Path(cwd) / ".aider.chat.history.md"]
    elif tool == "copilot":
        base = Path(os.environ.get("COPILOT_HOME", HOME / ".copilot"))
        paths = list(base.glob("session-state/*/events.jsonl"))
    elif tool == "opencode":
        base = HOME / ".local/share/opencode"
        paths = [p for p in base.glob("*.db*")] + [base / "storage"]
    best = 0.0
    for p in paths:
        try:
            best = max(best, p.stat().st_mtime)
        except OSError:
            pass
    return best


def gemini_agent(f, mtime, cwd_by_hash, cfg):
    """Gemini CLI session: ~/.gemini/tmp/<sha256(project root)>/chats/*.json."""
    try:
        data = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    msgs = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(msgs, list):
        return None
    info = {"tool": "gemini", "mtime": mtime, "model": None, "branch": None,
            "cwd": cwd_by_hash.get(f.parent.parent.name), "status": "busy",
            "action": "💭 thinking", "outcome": None}
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if m.get("model"):
            info["model"] = m["model"]
        for call in m.get("toolCalls") or []:
            label = action_for(call.get("name", ""), call.get("args"), False)
            st = str(call.get("status", "")).lower()
            if st in ("success", "error"):
                o = make_outcome(label, st == "success",
                                 iso_time(call.get("timestamp") or m.get("timestamp")))
                if o:
                    info["outcome"] = o
    last = next((m for m in reversed(msgs) if isinstance(m, dict)), None)
    if last and last.get("type") not in ("user",):
        calls = last.get("toolCalls") or []
        pending = [c for c in calls
                   if str(c.get("status", "")).lower() not in ("success", "error", "cancelled")]
        if pending:
            c = pending[-1]
            if str(c.get("status", "")).lower() in ("awaiting_approval", "validating"):
                info["status"], info["action"] = "waiting", "⏳ needs my approval"
            else:
                info["action"] = action_for(c.get("name", ""), c.get("args"), cfg["show_files"])
        elif time.time() - mtime > 15:
            info["status"], info["action"] = "waiting", "⏳ waiting for me"
        elif calls:
            info["action"] = action_for(calls[-1].get("name", ""), calls[-1].get("args"),
                                        cfg["show_files"])
    return info


def wsl_homes():
    """[(distro root, home)] for each user home in the WSL distros that are
    running, on Windows. Agents run in WSL keep their logs there."""
    if not IS_WIN:
        return []

    def fetch():
        try:
            out = subprocess.run(["wsl.exe", "-l", "--running", "-q"], capture_output=True,
                                 timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except Exception:
            return []
        text = out.decode("utf-16-le", "ignore") if b"\x00" in out else out.decode("utf-8", "ignore")
        homes = []
        for distro in filter(None, (x.strip().strip("\x00\ufeff") for x in text.splitlines())):
            for root in (rf"\\wsl.localhost\{distro}", rf"\\wsl$\{distro}"):
                try:
                    found = [h for h in (Path(root) / "home").iterdir() if h.is_dir()]
                except OSError:
                    continue
                try:
                    if (Path(root) / "root").is_dir():  # agents run as root in minimal distros
                        found.append(Path(root) / "root")
                except OSError:
                    pass
                homes += [(root, h) for h in found]
                break
        return homes
    return cached(CACHE, ("wsl",), 60, fetch)


def agent_homes():
    """[(WSL distro root or None, home)] to look for agent logs in."""
    return [(None, HOME)] + wsl_homes()


def wsl_path(root, path):
    """A Linux path from inside WSL as Windows can open it."""
    if not root or not path or not path.startswith("/"):
        return path
    m = re.match(r"/mnt/([a-zA-Z])(/.*)?$", path)
    if m:
        return m.group(1).upper() + ":\\" + (m.group(2) or "").lstrip("/").replace("/", "\\")
    return root + path.replace("/", "\\")


def gather_agents(cfg, procs=None):
    now = time.time()
    keep = now - max(cfg["agent_active_seconds"], cfg["agent_waiting_seconds"])
    agents = []

    def wanted(a):
        age = now - a["mtime"]
        return age < (cfg["agent_waiting_seconds"] if a["status"] == "waiting" or a.get("long_running")
                      else cfg["agent_active_seconds"])

    def session_mtime(f):
        """(newest write to a Claude session, newest write by its subagents).
        Subagents log in a folder named after the session."""
        m = f.stat().st_mtime
        sub_m = 0
        sub = f.with_suffix("")
        if now - m < 86400 and sub.is_dir():
            for g in sub.rglob("*.jsonl"):
                try:
                    sub_m = max(sub_m, g.stat().st_mtime)
                except OSError:
                    pass
        return max(m, sub_m), sub_m

    for root, home in agent_homes():
        found = []
        for base in home.glob(".claude*/projects"):
            for f in base.glob("*/*.jsonl"):
                try:
                    m, sub_m = session_mtime(f)
                except OSError:
                    continue
                if m >= keep:
                    a = claude_agent(f, m, cfg)
                    if now - sub_m < 60 and a["status"] == "waiting":
                        # Background subagents still working after the turn ended.
                        a["status"], a["action"] = "busy", "🤖 directing a subagent"
                    if wanted(a):
                        found.append(a)
        sessions = home / ".codex/sessions"
        if sessions.exists():
            for d in sorted(sessions.glob("*/*/*"))[-2:]:  # rollouts are date-sharded
                for f in d.glob("rollout-*.jsonl"):
                    try:
                        m = f.stat().st_mtime
                    except OSError:
                        continue
                    if m >= keep:
                        a = codex_agent(f, m, cfg)
                        if wanted(a):
                            found.append(a)
        for a in found:
            a["cwd"] = wsl_path(root, a["cwd"])
        agents += found

    # Gemini names project folders by sha256 of the project root; map the
    # hashes back using the working dirs of running gemini processes.
    gem = HOME / ".gemini/tmp"
    if gem.exists():
        import hashlib
        cwd_by_hash = {}
        for pid, (name, _, _, exe) in (procs or {}).items():
            if agent_tool_for(pid, name, exe) == "gemini":
                cwd = proc_cwd(pid)
                for c in filter(None, [cwd, resolve_project(cwd)[1]]):
                    cwd_by_hash[hashlib.sha256(c.encode()).hexdigest()] = c
        for f in gem.glob("*/chats/*.json"):
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if m >= keep:
                a = gemini_agent(f, m, cwd_by_hash, cfg)
                if a and wanted(a):
                    agents.append(a)
    return agents


# ------------------------------------------------------- git / GitHub ----

CACHE = {}


def git_user_email():
    return cached(CACHE, "email", 3600, lambda: run("git", "config", "--global", "user.email"))


def git_stats(root):
    def fetch():
        branch = run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root)
        added = removed = 0
        for line in run("git", "diff", "HEAD", "--numstat", cwd=root).splitlines():
            a, r, *_ = line.split("\t") + ["", ""]
            if a.isdigit() and r.isdigit():
                added, removed = added + int(a), removed + int(r)
        today = run("git", "log", "--since=midnight", f"--author={git_user_email()}",
                    "--oneline", "--all", cwd=root)
        return {"branch": branch, "added": added, "removed": removed,
                "commits_today": len(today.splitlines()) if today else 0}
    return cached(CACHE, ("git", root), 30, fetch)


def commit_days(root):
    def fetch():
        out = run("git", "log", "--since=120 days ago", f"--author={git_user_email()}",
                  "--all", "--format=%cs", cwd=root)
        return set(out.splitlines())
    return cached(CACHE, ("days", root), 3600, fetch)


def github_slug(root):
    url = cached(CACHE, ("remote", root), 3600,
                 lambda: run("git", "remote", "get-url", "origin", cwd=root))
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(\.git)?$", url or "")
    return m.group(1) if m else None


def gh_api(path):
    req = urllib.request.Request(f"https://api.github.com{path}",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "ai-presence"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except Exception:
        return None


def github_public(slug):
    """True if the repo is publicly visible (unauthenticated API can see it)."""
    return cached(CACHE, ("public", slug), 6 * 3600,
                  lambda: bool((gh_api(f"/repos/{slug}") or {}).get("html_url")))


def ci_status(slug, branch):
    def fetch():
        data = gh_api(f"/repos/{slug}/commits/{branch}/check-runs?per_page=50")
        runs = (data or {}).get("check_runs") or []
        if not runs:
            return None
        if any(r.get("status") != "completed" for r in runs):
            return "🟡 CI running"
        if any(r.get("conclusion") in ("failure", "timed_out", "cancelled") for r in runs):
            return "❌ CI failing"
        return "✅ CI green"
    return cached(CACHE, ("ci", slug, branch), 300, fetch)


# --------------------------------------------------------------- streak ----

def update_streak(live_today, roots):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "days.json"
    try:
        days = set(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError):
        days = set()
    today = date.today().isoformat()
    if live_today and today not in days:
        days.add(today)
        path.write_text(json.dumps(sorted(days)))
    for r in roots:
        days |= commit_days(r)
    d, streak = date.today(), 0
    if today not in days:  # streak still counts until you miss a whole day
        d -= timedelta(days=1)
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


# ---------------------------------------------------------- today stats ----
# Agent time and tokens since midnight, read incrementally from the logs.

STATS = {}          # path -> per-file running totals for today
STATS_AT = [0.0]    # last refresh
ACTIVE_GAP = 120    # seconds between log entries that still count as working


def _midnight():
    from datetime import datetime
    return datetime.combine(date.today(), datetime.min.time()).timestamp()


def _scan(path, tool, kind, midnight):
    """Read new lines of one log and fold them into STATS[path]."""
    st = STATS.get(path)
    if not st or st["day"] != midnight:
        st = STATS[path] = {"day": midnight, "offset": 0, "tool": tool, "secs": 0.0,
                            "tokens": 0, "last": None, "seen": set(), "codex_total": 0}
    try:
        size = path.stat().st_size
        if size < st["offset"]:
            st["offset"] = 0
        with open(path, "rb") as f:
            f.seek(st["offset"])
            chunk = f.read()
    except OSError:
        return
    end = chunk.rfind(b"\n") + 1  # leave a half-written last line for next time
    st["offset"] += end
    for raw in chunk[:end].splitlines():
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        t = iso_time(r.get("timestamp"))
        if not t or t < midnight:
            continue
        if kind == "claude" and r.get("cwd") and st["tool"] == "claude":
            st["tool"] = CLAUDE_ENTRYPOINTS.get(r.get("entrypoint"), "claude")
        if st["last"] and 0 < t - st["last"] < ACTIVE_GAP:
            st["secs"] += t - st["last"]
        st["last"] = max(t, st["last"] or t)
        if kind == "claude":
            msg = r.get("message") or {}
            u = msg.get("usage")
            # A reply is logged once per content block; count its usage once.
            if u and msg.get("id") not in st["seen"]:
                st["seen"].add(msg.get("id"))
                st["tokens"] += (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                                 + u.get("cache_creation_input_tokens", 0))
        else:
            info = (r.get("payload") or {}).get("info") or {}
            tot = (info.get("total_token_usage") or {})
            if tot:
                st["codex_total"] = (tot.get("input_tokens", 0) - tot.get("cached_input_tokens", 0)
                                     + tot.get("output_tokens", 0))
                st["tokens"] = st["codex_total"]


def today_stats(every=60):
    """{tool: (agent_seconds, tokens)} for today. Refreshes at most once a minute."""
    now = time.time()
    if now - STATS_AT[0] >= every:
        STATS_AT[0] = now
        midnight = _midnight()
        for _, home in agent_homes():
            for base in home.glob(".claude*/projects"):
                for f in base.glob("*/*.jsonl"):
                    try:
                        if f.stat().st_mtime >= midnight:
                            _scan(f, "claude", "claude", midnight)
                    except OSError:
                        pass
            sessions = home / ".codex/sessions"
            if sessions.exists():
                for d in sorted(sessions.glob("*/*/*"))[-2:]:
                    for f in d.glob("rollout-*.jsonl"):
                        try:
                            if f.stat().st_mtime >= midnight:
                                _scan(f, "chatgpt", "codex", midnight)
                        except OSError:
                            pass
    out = {}
    for st in STATS.values():
        secs, tok = out.get(st["tool"], (0.0, 0))
        out[st["tool"]] = (secs + st["secs"], tok + st["tokens"])
    return out


def human_tokens(n):
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            v = n / size
            return f"{v:.1f}{unit}".replace(".0" + unit, unit)
    return str(n)


def human_duration(secs):
    h, m = int(secs // 3600), int(secs % 3600 // 60)
    return f"{h}h {m}m" if h else f"{m}m"


# ----------------------------------------------------------- milestones ----

MONTH_MARKS = (25, 50, 100, 150, 200, 300, 500)
DAY_MARKS = (10, 25, 50)


def commit_times(root, since):
    def fetch():
        out = run("git", "log", f"--since={since}", f"--author={git_user_email()}",
                  "--all", "--format=%ct", cwd=root)
        return sorted(int(x) for x in out.split() if x.isdigit())
    return cached(CACHE, ("ctimes", root, since), 60, fetch)


def milestones(cfg, s, streak):
    """Recent achievements for this card, newest first: [(text, badge)]."""
    hidden, window, now = set(cfg["hide_projects"]), cfg["milestone_seconds"], time.time()
    found = []
    roots = [r for p, r in s["roots"].items() if p not in hidden]

    # Commit counts crossing round numbers (this month / today), across the card's repos.
    for since, marks, label in ((date.today().replace(day=1).isoformat(), MONTH_MARKS, "this month"),
                                ("midnight", DAY_MARKS, "today")):
        times = sorted(t for r in roots for t in commit_times(r, since))
        for n in marks:
            if len(times) >= n and now - times[n - 1] < window:
                found.append((times[n - 1], f"🏆 {n}th commit {label}", "streak_big"))

    # A new longest streak (the record is kept next to the day log).
    rec_path = STATE_DIR / "record.json"
    try:
        rec = json.loads(rec_path.read_text())
    except (OSError, ValueError):
        rec = {"best": 0, "set_at": 0}
    if streak > rec["best"]:
        if rec["best"]:  # don't celebrate the very first run
            rec["set_at"] = now
        rec["best"] = streak
        try:
            rec_path.write_text(json.dumps(rec))
        except OSError:
            pass
    if rec.get("set_at") and now - rec["set_at"] < window:
        found.append((rec["set_at"], f"🔥 new record: {streak}-day streak", "streak_big"))

    # Your pull requests merged on public GitHub repos.
    for p, r in s["roots"].items():
        if p in hidden:
            continue
        slug = github_slug(r)
        if not slug or not github_public(slug):
            continue
        pulls = cached(CACHE, ("pulls", slug), 300, lambda slug=slug: gh_api(
            f"/repos/{slug}/pulls?state=closed&sort=updated&direction=desc&per_page=10")) or []
        for pr in pulls if isinstance(pulls, list) else []:
            t = iso_time(pr.get("merged_at")) if pr.get("merged_at") else None
            if t and now - t < window:
                found.append((t, f"🎉 PR #{pr.get('number')} merged · {p}", "ci_green"))
    return [(text, badge) for _, text, badge in sorted(found, reverse=True)]


# ------------------------------------------------------------ snapshot ----

def snapshot(cfg):
    """{tool: {...}} for every app that's open or has agents."""
    now = time.time()
    procs, kids = proc_table()
    state = {t: {"running_since": None, "agents": [], "projects": {}, "roots": {},
                 "focused": False} for t in TOOLS}

    def add_project(tool, name, root, weight=1):
        if name:
            s = state[tool]
            s["projects"][name] = s["projects"].get(name, 0) + weight
            if root:
                s["roots"][name] = root

    # App launch time = oldest process of that app whose parent isn't the same app.
    for comm, ppid, start, exe in procs.values():
        tool = app_tool(comm, exe)
        if tool and procs.get(ppid, ("",))[0] != comm:
            rs = state[tool]["running_since"]
            state[tool]["running_since"] = min(rs, start) if rs else start

    agents = gather_agents(cfg, procs)
    # CLI agents found by process (Grok Build, Muse Code, and Gemini when it
    # has no session file yet). Busy while it's using CPU, else waiting on you.
    logged = {(a["tool"], a["cwd"]) for a in agents}
    for pid, (comm, ppid, start, exe) in procs.items():
        tool = agent_tool_for(pid, comm, exe)
        if tool not in PROC_AGENTS:
            continue
        parent = procs.get(ppid)
        if parent and (parent[0] == comm or agent_tool_for(ppid, parent[0], parent[3]) == tool):
            continue  # a worker/child of an agent we already counted
        cwd = proc_cwd(pid)
        rs = state[tool]["running_since"]
        state[tool]["running_since"] = min(rs, start) if rs else start
        if (tool, cwd) in logged or (tool == "gemini" and any(a["tool"] == "gemini" for a in agents)):
            continue
        # Working if it wrote its session/history file in the last 20 s, or is
        # using CPU; otherwise it's sitting at its prompt waiting for you.
        busy = now - activity_mtime(tool, cwd) < 20 or cpu_busy(pid)
        agents.append({"tool": tool, "mtime": now, "model": None, "cwd": cwd, "branch": None,
                       "status": "busy" if busy else "waiting",
                       "action": "⚡ working" if busy else "⏳ waiting for me", "outcome": None})
    for a in agents:
        a["project"], root = resolve_project(a["cwd"])
        state[a["tool"]]["agents"].append(a)
        add_project(a["tool"], a["project"], root, 2)  # agents rank above open windows

    win, windows = desktop_windows(procs)
    for w in windows:
        if w["tool"] in EDITORS:
            name = editor_project(w["title"])
            add_project(w["tool"], name, root_for_name(name) if name else None)

    if win:
        tool = win["tool"]
        if tool is None and win["terminal"] and win["pid"]:
            # A CLI agent running in the focused terminal.
            hits = [(procs[c][2], agent_tool_for(c, procs[c][0], procs[c][3]))
                    for c in descendants(kids, win["pid"]) if c in procs]
            hits = [h for h in hits if h[1]]
            if hits:
                tool = max(hits)[1]
        if tool:
            state[tool]["focused"] = True
            if tool in EDITORS:
                name = editor_project(win["title"])
                add_project(tool, name, root_for_name(name) if name else None, 100)

    live = {}
    for tool, s in state.items():
        if s["running_since"] or s["agents"]:
            s["projects"] = sorted(s["projects"], key=lambda p: -s["projects"][p])
            s["running_since"] = s["running_since"] or min(a["mtime"] for a in s["agents"])
            live[tool] = s
    return live


# ------------------------------------------------------------ styling ----

def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def fit(parts, limit=128):
    """Join project names with ✦, collapsing the tail into "+N more"."""
    for keep in range(len(parts), 0, -1):
        extra = len(parts) - keep
        s = SEP.join(parts[:keep]) + (f"{SEP}+{extra} more" if extra else "")
        if len(s) <= limit:
            return s
    return parts[0][: limit - 1] + "…"


def clip(s, n=128):
    return s if len(s) <= n else s[: n - 1] + "…"


# Line emoji -> small badge image
ACTION_BADGES = {"✍": "act_edit", "📖": "act_read", "🔍": "act_search", "🌐": "act_web",
                 "🤖": "act_subagent", "🖥": "act_browser", "🧪": "act_test", "🔨": "act_build",
                 "📦": "act_commit", "🚀": "act_push", "🛸": "act_sim", "⚙": "act_shell",
                 "⚡": "act_working", "💭": "act_thinking", "⏳": "act_waiting"}
CI_BADGES = {"✅": "ci_green", "❌": "ci_fail", "🟡": "ci_running"}


def url_exists(url):
    def fetch():
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ai-presence"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status == 200
        except Exception:
            return False
    return cached(CACHE, ("img", url), 6 * 3600, fetch)


def image(cfg, *keys):
    """First of `keys` that exists, as a Discord image (URL or asset key)."""
    if cfg["images"] == "assets":
        return keys[0]
    if cfg["images"] != "url" or not cfg["image_base_url"]:
        return None
    for key in keys:
        if key:
            for ext in ("jpg", "png"):
                url = f"{cfg['image_base_url'].rstrip('/')}/{key}.{ext}"
                if url_exists(url):
                    return url
    return None


def latest_outcome(s):
    outs = [a.get("outcome") for a in s["agents"] if a.get("outcome")]
    return max(outs, key=lambda o: o["t"]) if outs else None


def mood(s, cfg=DEFAULT_CONFIG):
    """Big-picture mood: failed > push > success > sim > crew > cooking > waiting > idle."""
    o, now = latest_outcome(s), time.time()
    if o and not o["ok"] and now - o["t"] < cfg["failed_mood_seconds"]:
        return "failed"
    if s.get("ci_failing"):
        return "failed"
    busy_actions = [a["action"] for a in s["agents"] if a["status"] == "busy"]
    if any(x.startswith("🚀") for x in busy_actions) or (
            o and o["ok"] and o["kind"] == "push" and now - o["t"] < cfg["success_mood_seconds"]):
        return "push"
    if o and o["ok"] and now - o["t"] < cfg["success_mood_seconds"]:
        return "success"
    if any(x.startswith("🛸") for x in busy_actions):
        return "sim"
    busy = len(busy_actions)
    if busy >= cfg["crew_size"]:
        return "crew"
    if busy:
        return "cooking"
    if s["agents"]:
        return "waiting"
    return "cooking" if s["focused"] else "idle"


MOOD_TEXT = {"failed": "something broke", "success": "it passed!", "crew": "the crew is cooking",
             "push": "shipped it 🚀", "sim": "flying the sim",
             "cooking": "cooking", "waiting": "waiting for me", "idle": "idle"}


# When an app has no art for a mood yet, fall back to the closest one it has.
MOOD_FALLBACK = {"failed": "waiting", "success": "cooking", "crew": "cooking",
                 "push": "success", "sim": "cooking"}


def shown_action(cfg, action):
    """An agent's action as this card may show it. Peers' actions arrive
    already rendered with their own settings, so file names are checked again."""
    m = re.search(r"\s+(\S*\.[A-Za-z0-9]{1,8})$", action)
    if m and (not cfg["show_files"] or SENSITIVE_FILE.search(m.group(1))):
        return strip_file(action)
    return action


def masked_projects(cfg, s):
    """Projects the Discord card calls "private project": the hidden ones,
    and unless show_private_projects, any that aren't public on GitHub."""
    hidden = set(cfg["hide_projects"])
    if cfg.get("show_private_projects"):
        return hidden
    return hidden | {p for p in s["projects"] if not is_public_project(
        s, p, next((a for a in s["agents"] if a.get("project") == p), None))}


def card_lines(cfg, tool, s, streak, machines=None, switched=None):
    """Every line this card can rotate through, as dicts of
    text / party ("(2 of 3)") / badge (small image) / hover (its tooltip)."""
    hidden = set(cfg["hide_projects"])
    masked = masked_projects(cfg, s)
    agents = s["agents"]
    busy = [a for a in agents if a["status"] == "busy"]
    waiting = [a for a in agents if a["status"] == "waiting"]
    count_badge = f"agents_{min(len(agents), 9)}" if agents else None
    count_hover = plural(len(agents), "agent") + " running" if agents else None
    lines = []

    def add(text, badge=None, hover=None, party=None):
        lines.append({"text": clip(text), "badge": badge, "hover": hover or text,
                      "party": party})

    # 0. Fresh milestones go first, so they're the next thing shown.
    if cfg["show_milestones"]:
        for text, badge in milestones(cfg, s, streak)[:2]:
            add(text, badge)

    # 1. Agent headline, with Discord's "(2 of 3)" party counter.
    if agents:
        if busy and waiting:
            text = f"⚡ {len(busy)} cooking · ⏳ {len(waiting)} waiting for me"
        elif busy:
            text = f"⚡ {plural(len(busy), 'agent')} cooking"
        else:
            text = f"⏳ {plural(len(waiting), 'agent')} waiting for me"
        per_tool = {}
        for a in agents:
            per_tool[a["tool"]] = per_tool.get(a["tool"], 0) + 1
        if len(per_tool) > 1:  # combined card: "· Claude 2 · Codex 1"
            text += "".join(f" · {TOOL_NAMES[t].split(' ·')[0]} {n}"
                            for t, n in sorted(per_tool.items(), key=lambda kv: -kv[1]))
        add(text, count_badge, count_hover, [len(busy), len(agents)] if len(agents) > 1 else None)
    elif s["focused"]:
        add("● hands on keyboard")
    else:
        add("○ open in the background")

    # 1b. On the combined card: which tools are open right now.
    if len(s.get("tools", [])) > 1:
        add("🧰 " + " · ".join(TOOL_NAMES[t].split(" ·")[0] for t in s["tools"]))

    # 1c. Which computer(s): "💻 MacBook Pro · macOS 15", or with several
    # linked ones "🖥 2 machines: 💻 MacBook Pro (3) · 🖥 Aurora R16 (2)".
    for i, (text, hover) in enumerate(machine_lines(cfg, machines or [], switched)):
        if switched and i == 0:
            lines.insert(0, {"text": clip(text), "badge": None, "hover": hover, "party": None})
        else:
            add(text, None, hover)

    # 2. What each busy agent is doing right now (newest first, up to 3).
    for a in sorted(busy, key=lambda a: -a["mtime"])[:3] if cfg["show_actions"] else []:
        action = shown_action(cfg, a["action"])
        where = a["project"] if a["project"] and a["project"] not in masked else None
        text = f"{action} · {where}" if where and cfg["show_project"] else action
        add(text, ACTION_BADGES.get(action.split()[0]), action)

    # 3. Result of the newest test/build run (within the last 30 minutes).
    o = latest_outcome(s) if cfg["show_actions"] else None
    if o and time.time() - o["t"] < 1800:
        label = {("tests", True): "✅ tests passed", ("tests", False): "❌ tests failing",
                 ("build", True): "✅ build passed", ("build", False): "❌ build failed",
                 ("push", True): "🚀 pushed", ("push", False): "❌ push rejected"}[(o["kind"], o["ok"])]
        add(label, "ci_green" if o["ok"] else "ci_fail")

    # 3b. Agent time and tokens today for this app.
    if cfg["show_stats"]:
        stats = today_stats()
        secs = sum(stats.get(t, (0, 0))[0] for t in s.get("tools", [tool]))
        tok = sum(stats.get(t, (0, 0))[1] for t in s.get("tools", [tool]))
        bits = []
        if secs >= 60:
            bits.append(f"⏱ {human_duration(secs)} of agent time today")
        if tok:
            bits.append(f"💸 {human_tokens(tok)} tokens")
        if bits:
            add(" · ".join(bits), "act_working")

    # 4. Git for the top project, then streak / commits, then model + CI.
    top = next((p for p in s["projects"] if p not in masked and s["roots"].get(p)), None)
    root = s["roots"].get(top) if top else None
    git = git_stats(root) if root else None
    if cfg["show_git"] and git and git["branch"]:
        diff = f" · +{git['added']} −{git['removed']}" if git["added"] or git["removed"] else " · clean"
        add(f"🌿 {git['branch']}{diff}", "git", f"{top} on {git['branch']}")

    bits = []
    if cfg["show_streak"] and streak >= 2:
        bits.append(f"🔥 {streak}-day streak")
    commits = sum(git_stats(r)["commits_today"] for p, r in s["roots"].items() if p not in hidden)
    if commits and cfg["show_stats"]:
        bits.append(f"📦 {plural(commits, 'commit')} today")
    if bits:
        badge = ("streak_big" if streak >= 7 else "streak") if streak >= 2 else "act_commit"
        add(" · ".join(bits), badge)

    bits, badge = [], None
    models = [a["model"] for a in agents if a["model"]]
    if cfg["show_model"] and models:
        top_model = max(set(models), key=models.count)
        bits.append(f"🧠 {pretty_model(top_model)}")
        badge = next((f"model_{k}" for k in ("claude", "gemini", "muse", "grok")
                      if k in top_model.lower()), "model_gpt")
    slug = github_slug(root) if root else None
    if cfg["show_ci"] and slug and git and git["branch"] and github_public(slug):
        ci = ci_status(slug, git["branch"])
        if ci:
            bits.append(ci)
            s["ci_failing"] = ci.startswith("❌")
            badge = CI_BADGES.get(ci.split()[0], badge)
    if bits:
        add(" · ".join(bits), badge)

    for line in lines:  # fall back to the agent count when a line has no badge
        if not line["badge"]:
            line["badge"], line["hover"] = count_badge, count_hover
    return lines, top, slug


def build_activity(cfg, tool, s, totals, streak, tick, machines=None, switched=None):
    masked = masked_projects(cfg, s)
    projects = list(dict.fromkeys(
        "private project" if p in masked else p for p in s["projects"]))
    agents = s["agents"]
    busy = [a for a in agents if a["status"] == "busy"]

    if cfg["show_project"] and projects:
        details = "▸ " + fit(projects, 126)
    elif cfg["show_project"]:
        details = "▸ no repo open"
    else:
        details = f"Building with {TOOL_NAMES[tool]}"

    lines, top, slug = card_lines(cfg, tool, s, streak, machines, switched)
    line = lines[tick % len(lines)]

    kind = PLAYING
    if cfg["mood_verbs"]:
        if len(agents) >= 5:
            kind = COMPETING          # "Competing in Claude"
        elif busy and not s["focused"]:
            kind = WATCHING           # "Watching Claude" while it cooks

    act = {"type": kind, "details": details, "state": line["text"],
           "timestamps": {"start": int(s["running_since"])}}
    if line["party"]:
        act["party"] = {"id": f"ai-presence-{tool}", "size": line["party"]}

    buttons = []
    if cfg["github_button"] and slug and top and github_public(slug):
        label = f"⭐ {top} on GitHub"
        buttons.append({"label": label if len(label) <= 32 else "⭐ View on GitHub",
                        "url": f"https://github.com/{slug}"})
    if cfg["promo_button"]:
        buttons.append({"label": "🤖 Get Agent Presence", "url": LANDING_URL})
    if buttons:
        act["buttons"] = buttons[:2]

    # Big picture: the app in its current mood. Small badge: follows the line.
    m = mood(s, cfg)
    # The sim control room has no app logo in it, so every tool can share it.
    large = image(cfg, f"{tool}_{m}", "claude_sim" if m == "sim" else None,
                  f"{tool}_{MOOD_FALLBACK.get(m, m)}", tool, "logo")
    if large:
        across = (f"{plural(totals['agents'], 'agent')} across {plural(totals['tools'], 'app')}"
                  if totals["tools"] > 1 and not s.get("tools")
                  else f"{TOOL_NAMES[tool]} · {MOOD_TEXT[m]}")
        here = next((x for x in machines or [] if x["me"]), None)
        if here and cfg["show_machine"]:
            across += f" · on {here['label']}"
        act["assets"] = {"large_image": large, "large_text": clip(across)}
        small = image(cfg, line["badge"], f"agents_{min(len(agents), 9)}" if agents else None)
        if small:
            act["assets"]["small_image"] = small
            act["assets"]["small_text"] = clip(line["hover"] or "")
    return act


# ------------------------------------------------------- machines ----
# Which computer this is, how long since you touched it, and (with a link
# key) what your other computers are doing. Linked machines share a small
# status packet over the local network (and, with a web repo, an encrypted
# copy through GitHub for computers on other networks); the one you used most
# recently shows the card, with every machine's agents on it.

MAC_OS_NAMES = {26: "Tahoe", 15: "Sequoia", 14: "Sonoma", 13: "Ventura", 12: "Monterey", 11: "Big Sur"}
JUNK_MODELS = {"", "to be filled by o.e.m.", "system product name", "default string",
               "not applicable", "none", "o.e.m.", "type1productconfigid"}
LAPTOP_CHASSIS = {"8", "9", "10", "14", "30", "31", "32"}  # DMI chassis types
LINK_PORT = 47777
PEER_TTL = 20            # seconds a peer stays known without a packet
IN_USE_SECONDS = 120     # touched this recently = "in use"
SWITCH_MARGIN = 15       # hysteresis before the card moves to another machine
RELAY_PREFIX = "agent-presence-peer-"  # one branch per computer in the web repo
RELAY_SECONDS = 30       # how often the relay writes this computer's packet
RELAY_TTL = 150          # a relayed packet older than this is ignored
_machine = {}


def _read(path):
    try:
        return Path(path).read_text(errors="ignore").replace("\x00", "").strip()
    except OSError:
        return ""


def _clean_model(s):
    s = " ".join((s or "").split())
    return "" if s.lower() in JUNK_MODELS else s


def machine_info():
    """{"id", "model", "chip", "kind" (laptop/desktop/board), "os", "os_full"} for this computer.
    Computed once: system_profiler / PowerShell are slow."""
    if _machine:
        return _machine
    model, chip, kind, os_short, os_full = "", "", "desktop", "", ""
    import platform
    if IS_MAC:
        try:
            hw = json.loads(run("system_profiler", "-json", "SPHardwareDataType", timeout=20) or "{}")
            hw = (hw.get("SPHardwareDataType") or [{}])[0]
        except ValueError:
            hw = {}
        model = hw.get("machine_name") or run("sysctl", "-n", "hw.model")
        chip = hw.get("chip_type") or hw.get("cpu_type") or ""
        kind = "laptop" if "macbook" in model.lower() else "desktop"
        ver = run("sw_vers", "-productVersion") or platform.mac_ver()[0]
        head = (ver or "").split(".")[0]
        major = int(head) if head.isdigit() else 0
        os_short = f"macOS {major}" if major else "macOS"
        os_full = " ".join(filter(None, ["macOS", MAC_OS_NAMES.get(major), ver]))
    elif IS_LINUX:
        board = _read("/proc/device-tree/model")  # Raspberry Pi, Jetson, other SBCs
        if board:
            model, kind = re.sub(r"\s+(Rev|Developer Kit).*$", "", board), "board"
        else:
            vendor = _clean_model(_read("/sys/class/dmi/id/sys_vendor"))
            name = _clean_model(_read("/sys/class/dmi/id/product_name"))
            if vendor.lower().startswith("lenovo"):  # product_name is a part number
                name = _clean_model(_read("/sys/class/dmi/id/product_version")) or name
            vendor = re.sub(r"\s*(Inc\.?|Corporation|Co\., Ltd\.?|Technology.*)$", "", vendor)
            model = name if not vendor or name.lower().startswith(vendor.lower().split()[0]) \
                else f"{vendor} {name}"
            if _read("/sys/class/dmi/id/chassis_type") in LAPTOP_CHASSIS:
                kind = "laptop"
        rel = {}
        for line in _read("/etc/os-release").splitlines():
            k, _, v = line.partition("=")
            rel[k] = v.strip('"')
        os_short = " ".join(filter(None, [rel.get("NAME", "Linux").replace(" GNU/Linux", ""),
                                          rel.get("VERSION_ID")]))
        os_full = rel.get("PRETTY_NAME") or os_short
    elif IS_WIN:
        build = int((platform.version().split(".") + ["0", "0", "0"])[2] or 0)
        os_short = "Windows 11" if build >= 22000 else f"Windows {platform.release()}"
        os_full = f"{os_short} (build {build})"
        out = run("powershell", "-NoProfile", "-Command",
                  "$c = Get-CimInstance Win32_ComputerSystem; "
                  "\"$($c.Manufacturer)|$($c.Model)|$($c.PCSystemType)\"", timeout=20)
        parts = (out or "").split("|")
        if len(parts) == 3:
            vendor, name = _clean_model(parts[0]), _clean_model(parts[1])
            vendor = re.sub(r"\s*(Inc\.?|Corporation)$", "", vendor)
            model = name if not vendor or name.lower().startswith(vendor.lower().split()[0]) \
                else f"{vendor} {name}"
            kind = "laptop" if parts[2].strip() == "2" else "desktop"
    # Never fall back to the hostname: it often has your real name in it.
    generic = "Mac" if IS_MAC else "Windows PC" if IS_WIN else "Linux PC"
    _machine.update(id=machine_id(), model=model or generic,
                    chip=chip, kind=kind, os=os_short or platform.system(),
                    os_full=os_full or platform.platform())
    return _machine


def machine_id():
    """A random ID for this computer, kept across restarts (never the hostname)."""
    path = STATE_DIR / "machine-id"
    try:
        return path.read_text().strip()
    except OSError:
        mid = uuid.uuid4().hex[:12]
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(mid)
        return mid


def machine_label(m, cfg=None):
    """"MacBook Pro" or the machine_name from config."""
    if cfg and cfg.get("machine_name"):
        return cfg["machine_name"]
    return m.get("name") or m["model"]


def machine_icon(m):
    return {"laptop": "💻", "board": "🔌"}.get(m.get("kind"), "🖥")


_idle_cache = {}


def input_idle():
    """Seconds since the last keyboard/mouse input on this computer, or None if unknown."""
    hit = _idle_cache.get("v")
    if hit and time.time() - hit[0] < 3:
        return hit[1]
    secs = None
    try:
        if IS_MAC:
            m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', run("ioreg", "-c", "IOHIDSystem", "-d", "4") or "")
            secs = int(m.group(1)) / 1e9 if m else None
        elif IS_WIN:
            import ctypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
            li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
                secs = ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000
        elif os.environ.get("DISPLAY"):
            out = run("xprintidle")  # optional: sudo apt install xprintidle
            secs = int(out) / 1000 if out and out.isdigit() else None
    except Exception:
        secs = None
    _idle_cache["v"] = (time.time(), secs)
    return secs


_discord_cache = {}


def discord_running():
    hit = _discord_cache.get("v")
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    found = False
    for path in ipc_paths():
        if IS_WIN:
            try:
                open(path, "r+b", buffering=0).close()
                found = True
                break
            except OSError:
                continue
        elif os.path.exists(path):
            found = True
            break
    _discord_cache["v"] = (time.time(), found)
    return found


def machine_status(cfg, live):
    """This computer's packet: who it is, how idle, and its agents."""
    m = machine_info()
    hidden = set(cfg["hide_projects"])
    agents = []
    for tool, s in live.items():
        for a in s["agents"]:
            proj = a.get("project")
            root = s["roots"].get(proj)
            slug = github_slug(root) if root else None
            agents.append({"tool": tool, "status": a["status"], "action": a["action"],
                           "project": None if proj in hidden else proj,
                           "public": bool(slug and github_public(slug)),
                           "model": a.get("model"), "mtime": a["mtime"],
                           "outcome": a.get("outcome")})
    agents.sort(key=lambda a: -a["mtime"])
    tools = {t: {"running_since": s["running_since"], "focused": s["focused"],
                 "projects": [p for p in s["projects"] if p not in hidden][:4]}
             for t, s in live.items()}
    idle = input_idle()
    return {"v": 1, "id": m["id"], "name": cfg.get("machine_name") or "", "model": m["model"],
            "chip": m["chip"], "kind": m["kind"], "os": m["os"], "os_full": m["os_full"],
            "idle": None if idle is None else round(idle, 1), "discord": discord_running(),
            "web": bool(web_cfg(cfg)["enabled"] and web_cfg(cfg)["repo"]),
            "agents": agents[:12], "tools": tools}


class Sealer:
    """Encrypts and signs packets with the link key: an HMAC-SHA256 keystream
    with a random nonce, then HMAC-SHA256 over nonce + ciphertext."""

    def __init__(self, key, purpose):
        import hashlib
        self.enc = hashlib.sha256(f"agent-presence {purpose} enc:".encode() + key.encode()).digest()
        self.mac = hashlib.sha256(f"agent-presence {purpose} mac:".encode() + key.encode()).digest()

    def _stream(self, nonce, n):
        import hmac
        out = bytearray()
        i = 0
        while len(out) < n:
            out += hmac.new(self.enc, nonce + i.to_bytes(8, "big"), "sha256").digest()
            i += 1
        return bytes(out[:n])

    def seal(self, body):
        import hmac
        nonce = os.urandom(16)
        ct = bytes(a ^ b for a, b in zip(body, self._stream(nonce, len(body))))
        return hmac.new(self.mac, nonce + ct, "sha256").digest() + nonce + ct

    def open(self, raw):
        """The plaintext, or None if it wasn't sealed with this key."""
        import hmac
        tag, nonce, ct = raw[:32], raw[32:48], raw[48:]
        if len(raw) < 48 or not hmac.compare_digest(
                tag, hmac.new(self.mac, nonce + ct, "sha256").digest()):
            return None
        return bytes(a ^ b for a, b in zip(ct, self._stream(nonce, len(ct))))


class Link:
    """Shares machine_status() with your other computers over UDP broadcast.
    Packets are encrypted and signed with the link key, so others on the
    network can't read them and only your machines are trusted."""

    def __init__(self, key, port=LINK_PORT, target="<broadcast>"):
        self.box = Sealer(key, "link")
        self.port, self.target = port, target
        self.peers = {}  # id -> (received_at, packet)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", port))
        self.sock.setblocking(False)

    def seal(self, packet):
        body = json.dumps({**packet, "t": round(time.time(), 1)}, separators=(",", ":")).encode()
        return self.box.seal(body)

    def open(self, data):
        body = self.box.open(data)
        if body is None:
            return None
        try:
            pkt = json.loads(body)
        except ValueError:
            return None
        if not isinstance(pkt, dict) or abs(time.time() - pkt.get("t", 0)) > 60:
            return None  # stale or replayed (allows a minute of clock drift)
        return pkt

    def send(self, packet):
        try:
            self.sock.sendto(self.seal(packet), (self.target, self.port))
        except OSError as e:  # no network, or blocked (macOS Local Network permission)
            log("link: send failed:", e)

    def receive(self, my_id):
        while True:
            try:
                data, _ = self.sock.recvfrom(65535)
            except (BlockingIOError, OSError):
                break
            pkt = self.open(data)
            if pkt and pkt.get("id") and pkt["id"] != my_id:
                self.peers[pkt["id"]] = (time.time(), pkt)
        now = time.time()
        self.peers = {k: v for k, v in self.peers.items() if now - v[0] < PEER_TTL}
        return [p for _, p in self.peers.values()]


def relay_wanted(cfg):
    w = web_cfg(cfg)
    return bool(cfg["link_key"] and w["enabled"] and w["repo"] and w["relay"])


class Relay:
    """Shares machine_status() with your linked computers on other networks,
    through the web repo. Each computer keeps one file on its own
    agent-presence-peer-<id> branch, encrypted and signed with the link key, so
    the public repo shows nothing readable and nobody else can post to it."""

    def __init__(self, key, cfg):
        import threading
        self.box = Sealer(key, "relay")
        self.cfg, self.repo = cfg, web_cfg(cfg)["repo"]
        self.peers = {}  # id -> packet
        self.seen = {}   # branch -> commit already read
        self.last_at, self.busy = 0, threading.Lock()
        self.thread_cls = threading.Thread

    def seal(self, packet):
        import base64
        body = json.dumps({**packet, "t": round(time.time(), 1)}, separators=(",", ":")).encode()
        return base64.b64encode(self.box.seal(body)).decode()

    def open(self, text):
        import base64
        import binascii
        try:
            body = self.box.open(base64.b64decode(text.strip(), validate=True))
        except (binascii.Error, ValueError):
            return None
        if body is None:
            return None
        try:
            pkt = json.loads(body)
        except ValueError:
            return None
        return pkt if isinstance(pkt, dict) and pkt.get("id") else None

    def sync(self, me, token):
        """Write this computer's packet, then read the others' (blocking)."""
        import base64
        publish_files(token, self.repo, RELAY_PREFIX + me["id"], {"peer.txt": self.seal(me) + "\n"})
        refs = github_call(token, "GET", f"/repos/{self.repo}/git/matching-refs/heads/{RELAY_PREFIX}")
        for ref in refs:
            branch, sha = ref["ref"][len("refs/heads/"):], ref["object"]["sha"]
            if branch == RELAY_PREFIX + me["id"] or self.seen.get(branch) == sha:
                continue
            got = github_call(token, "GET", f"/repos/{self.repo}/contents/peer.txt?ref={sha}")
            self.seen[branch] = sha
            pkt = self.open(base64.b64decode(got.get("content", "")).decode("ascii", "replace"))
            if pkt and pkt["id"] != me["id"]:
                self.peers[pkt["id"]] = pkt

    def update(self, me):
        """sync() in the background, at most every RELAY_SECONDS."""
        if time.time() - self.last_at < RELAY_SECONDS or not self.busy.acquire(blocking=False):
            return
        self.last_at = time.time()

        def go():
            try:
                token = github_token(self.cfg)
                if token:
                    self.sync(me, token)
                else:
                    log("relay: no GitHub token; run `agent-presence web setup`")
            except Exception as e:
                log("relay: sync failed:", repr(e))
            finally:
                self.busy.release()
        self.thread_cls(target=go, daemon=True).start()

    def receive(self, my_id):
        now = time.time()
        return [p for p in list(self.peers.values())
                if p["id"] != my_id and now - p.get("t", 0) < RELAY_TTL]


def pick_leader(me, peers, current=None, need="discord"):
    """ID of the machine that should show the card: the one you touched most
    recently, among machines with Discord open (or, with need="web", among
    machines that publish the web card). Sticks with the current one until
    another is clearly more recent, so it doesn't flicker."""
    every = [me] + list(peers)
    able = [p for p in every if p.get(need)] or [me]

    def idle(p):
        return p["idle"] if p.get("idle") is not None else 3600

    def busy(p):
        return sum(a["status"] == "busy" for a in p.get("agents", []))
    best = min(able, key=lambda p: (idle(p), -busy(p), p["id"]))
    cur = next((p for p in able if p["id"] == current), None)
    if cur and cur is not best and idle(cur) - idle(best) < SWITCH_MARGIN:
        return cur["id"]
    return best["id"]


def merge_peers(live, peers):
    """Add other machines' agents and open tools into this machine's snapshot."""
    for p in peers:
        label = p.get("name") or p.get("model") or "another computer"
        for tool, t in p.get("tools", {}).items():
            if tool not in TOOLS:
                continue
            s = live.setdefault(tool, {"running_since": t["running_since"], "agents": [],
                                       "projects": [], "roots": {}, "focused": False})
            s["running_since"] = min(s["running_since"], t["running_since"])
            for proj in t.get("projects", []):
                if proj not in s["projects"]:
                    s["projects"].append(proj)
        for a in p.get("agents", []):
            if a.get("tool") in TOOLS and a["tool"] in live:
                live[a["tool"]]["agents"].append({**a, "cwd": None, "branch": None,
                                                  "machine": label})
    return live


def machines_summary(me, peers, cfg):
    """[{label, icon, os, agents, busy, in_use, me}] for machines worth showing."""
    out = []
    for p in [me] + list(peers):
        agents = p.get("agents", [])
        idle = p.get("idle")
        in_use = idle is not None and idle < IN_USE_SECONDS
        if not (agents or in_use or p is me):
            continue
        out.append({"label": (cfg.get("machine_name") if p is me else p.get("name")) or p["model"],
                    "icon": machine_icon(p), "os": p.get("os", ""), "os_full": p.get("os_full", ""),
                    "agents": len(agents), "busy": sum(a["status"] == "busy" for a in agents),
                    "in_use": in_use, "me": p is me,
                    # When this machine was last verified: now for this computer,
                    # the packet's own send time for a linked one (None if the
                    # packet carried none). Presence consumers fail closed on it.
                    "last_seen_at": _iso_utc(time.time() if p is me else p.get("t"))})
    return out


def machine_lines(cfg, machines, switched=None):
    """Card lines about your computers: [(text, hover)]."""
    if not cfg.get("show_machine", True) or not machines:
        return []
    lines = []
    if switched:
        lines.append((f"↪ moved to {switched}", f"switched to {switched}"))
    if len(machines) == 1:
        m = machines[0]
        lines.append((f"{m['icon']} {m['label']} · {m['os']}", f"{m['label']} · {m['os_full']}"))
        return lines
    using = sum(m["in_use"] for m in machines)
    head = f"🖥 {plural(len(machines), 'machine')}" + (" in use" if using == len(machines) else "")
    parts = []
    for m in machines:
        bit = f"{m['icon']} {m['label']}"
        if m["agents"]:
            bit += f" ({m['agents']})"
        elif not m["in_use"]:
            bit += " 💤"
        parts.append(bit)
    lines.append((head + ": " + " · ".join(parts),
                  " · ".join(f"{m['label']}: {m['os_full']}" for m in machines)))
    away = [m for m in machines if m["busy"] and not m["in_use"] and not m["me"]]
    if away:
        m = max(away, key=lambda m: m["busy"])
        lines.append((f"{m['icon']} {m['label']}: {plural(m['busy'], 'agent')} cooking while I'm away", None))
    return lines


# ----------------------------------------------------------- habits ----
# A private, local log of your coding habits: one record per day, sampled
# once a minute. It never leaves this computer; `agent-presence stats` reads
# it, and the web card only gets the highlights you choose to share.

HABITS_DIR = STATE_DIR / "habits"
SESSION_GAP = 15  # minutes without activity that end a session
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _blank_day():
    return {"active_minutes": 0, "agent_minutes": 0, "max_parallel": 0, "hours": [0] * 24,
            "sessions": [], "tools": {}, "projects": {}, "machines": {}, "models": {},
            "outcomes": {}, "commits": {}, "tokens": 0, "agent_seconds": 0, "seen": []}


def load_habits(days=None):
    """{"YYYY-MM-DD": record} for the last `days` days (all when None)."""
    out = {}
    if not HABITS_DIR.exists():
        return out
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat() if days else ""
    for f in sorted(HABITS_DIR.glob("*.json")):
        if cutoff and f.stem < cutoff[:7]:
            continue
        try:
            month = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for day, rec in month.items():
            if day >= cutoff:
                out[day] = {**_blank_day(), **rec}
    return out


class Habits:
    """Adds one minute of observations at a time to today's record."""

    def __init__(self):
        self.minute = None
        self.month, self.data = None, {}

    def _day(self, day):
        month = day[:7]
        if month != self.month:
            self.month = month
            try:
                self.data = json.loads((HABITS_DIR / f"{month}.json").read_text())
            except (OSError, ValueError):
                self.data = {}
        rec = self.data.setdefault(day, _blank_day())
        for k, v in _blank_day().items():
            rec.setdefault(k, v)
        return rec

    def record(self, cfg, live, machine_label=None, now=None):
        now = now or time.time()
        minute = int(now // 60)
        if minute == self.minute:
            return False
        self.minute = minute
        t = time.localtime(now)
        rec = self._day(time.strftime("%Y-%m-%d", t))
        busy = [(tool, a) for tool, s in live.items() for a in s["agents"] if a["status"] == "busy"]
        focused = [tool for tool, s in live.items() if s["focused"]]
        if focused or busy:
            rec["active_minutes"] += 1
            rec["hours"][t.tm_hour] += 1
            if rec["sessions"] and minute - rec["sessions"][-1][1] <= SESSION_GAP:
                rec["sessions"][-1][1] = minute
            else:
                rec["sessions"].append([minute, minute])
            for tool in set(focused) | {tool for tool, _ in busy}:
                rec["tools"][tool] = rec["tools"].get(tool, 0) + 1
            projects = {a.get("project") for _, a in busy if a.get("project")}
            for tool in focused:
                if live[tool]["projects"]:
                    projects.add(live[tool]["projects"][0])
            for p in projects:
                rec["projects"][p] = rec["projects"].get(p, 0) + 1
            if machine_label:
                rec["machines"][machine_label] = rec["machines"].get(machine_label, 0) + 1
        rec["agent_minutes"] += len(busy)
        rec["max_parallel"] = max(rec["max_parallel"], len(busy))
        for _, a in busy:
            if a.get("model"):
                m = pretty_model(a["model"])
                rec["models"][m] = rec["models"].get(m, 0) + 1
        seen = set(rec["seen"])
        for s in live.values():
            for a in s["agents"]:
                o = a.get("outcome")
                if not o or o["t"] < _midnight():
                    continue
                key = f"{a['tool']}:{o['kind']}:{int(o['t'])}"
                if key not in seen:
                    seen.add(key)
                    k = f"{o['kind']}_{'pass' if o['ok'] else 'fail'}"
                    rec["outcomes"][k] = rec["outcomes"].get(k, 0) + 1
        rec["seen"] = sorted(seen)[-300:]
        stats = today_stats()
        rec["tokens"] = max(rec["tokens"], sum(v[1] for v in stats.values()))
        rec["agent_seconds"] = max(rec["agent_seconds"], int(sum(v[0] for v in stats.values())))
        for s in live.values():
            for p, root in s["roots"].items():
                g = git_stats(root)
                if g and g["commits_today"]:
                    rec["commits"][p] = max(rec["commits"].get(p, 0), g["commits_today"])
        try:
            HABITS_DIR.mkdir(parents=True, exist_ok=True)
            tmp = HABITS_DIR / f"{self.month}.json.tmp"
            tmp.write_text(json.dumps(self.data, separators=(",", ":")))
            tmp.replace(HABITS_DIR / f"{self.month}.json")
        except OSError as e:
            log("habits: can't save:", e)
        return True


def _streaks(active_days):
    """(current, best) runs of consecutive days."""
    days = sorted(active_days)
    best = run = 0
    prev = None
    for d in days:
        cur = date.fromisoformat(d)
        run = run + 1 if prev and cur - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = cur
    current, d = 0, date.today()
    if d.isoformat() not in active_days:
        d -= timedelta(days=1)
    while d.isoformat() in active_days:
        current += 1
        d -= timedelta(days=1)
    return current, best


def habit_summary(habits=None, today=None):
    """Totals and patterns over today, the last 7 and 30 days, and all time."""
    habits = load_habits() if habits is None else habits
    today = today or date.today()

    def window(n):
        lo = (today - timedelta(days=n - 1)).isoformat()
        return {d: r for d, r in habits.items() if lo <= d <= today.isoformat()}

    def total(recs):
        sessions = [b - a + 1 for r in recs.values() for a, b in r["sessions"]]
        outcomes = {}
        for r in recs.values():
            for k, v in r["outcomes"].items():
                outcomes[k] = outcomes.get(k, 0) + v
        tests = outcomes.get("tests_pass", 0) + outcomes.get("tests_fail", 0)

        def top(key, n=5):
            agg = {}
            for r in recs.values():
                for k, v in r[key].items():
                    agg[k] = agg.get(k, 0) + v
            return sorted(agg.items(), key=lambda kv: -kv[1])[:n]
        return {"days": sum(1 for r in recs.values() if r["active_minutes"] or r["commits"]),
                "active_minutes": sum(r["active_minutes"] for r in recs.values()),
                "agent_minutes": sum(r["agent_minutes"] for r in recs.values()),
                "max_parallel": max([r["max_parallel"] for r in recs.values()] or [0]),
                "commits": sum(sum(r["commits"].values()) for r in recs.values()),
                "tokens": sum(r["tokens"] for r in recs.values()),
                "sessions": len(sessions),
                "avg_session": round(sum(sessions) / len(sessions)) if sessions else 0,
                "longest_session": max(sessions or [0]),
                "outcomes": outcomes,
                "tests_run": tests,
                "tests_pass_rate": round(100 * outcomes.get("tests_pass", 0) / tests) if tests else None,
                "projects": top("projects"), "tools": top("tools"), "models": top("models", 3),
                "machines": top("machines", 4)}

    active = {d for d, r in habits.items() if r["active_minutes"] or r["commits"]}
    try:
        active |= set(json.loads((STATE_DIR / "days.json").read_text()))
    except (OSError, ValueError):
        pass
    current, best = _streaks(active)
    hours, weekdays = [0] * 24, [0] * 7
    for d, r in window(30).items():
        for h, m in enumerate(r["hours"]):
            hours[h] += m
        weekdays[date.fromisoformat(d).weekday()] += r["active_minutes"]
    return {"today": total(window(1)), "week": total(window(7)), "month": total(window(30)),
            "all": total(habits), "streak": current, "best_streak": best,
            "first_day": min(habits) if habits else None,
            "hours": hours, "weekdays": weekdays,
            "peak_hour": max(range(24), key=lambda h: hours[h]) if any(hours) else None,
            "peak_weekday": WEEKDAYS[max(range(7), key=lambda d: weekdays[d])] if any(weekdays) else None,
            "daily": [(d, habits[d]["active_minutes"], sum(habits[d]["commits"].values()))
                      for d in sorted(window(30))]}


def _hours(minutes):
    return human_duration(minutes * 60) if minutes else "0m"


def _clock(h):
    return f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}"


def cmd_stats(html=False):
    s = habit_summary()
    if not s["first_day"]:
        print("No habits recorded yet. They build up while agent-presence runs.")
        return
    t, w, m, a = s["today"], s["week"], s["month"], s["all"]
    print(f"Coding habits since {s['first_day']} (private: stored only on this computer)\n")
    print(f"  streak        {plural(s['streak'], 'day')} (best {s['best_streak']})")
    for label, x in (("today", t), ("7 days", w), ("30 days", m), ("all time", a)):
        bits = [f"{_hours(x['active_minutes'])} coding", f"{_hours(x['agent_minutes'])} of agent work",
                plural(x["commits"], "commit")]
        if x["tokens"]:
            bits.append(f"{human_tokens(x['tokens'])} tokens")
        print(f"  {label:<13} " + " · ".join(bits))
    print(f"\n  sessions      {m['sessions']} in 30 days · average {_hours(m['avg_session'])}"
          f" · longest {_hours(m['longest_session'])}")
    if s["peak_hour"] is not None:
        print(f"  peak time     around {_clock(s['peak_hour'])} · most on {s['peak_weekday']}s")
    if m["max_parallel"]:
        print(f"  most agents   {m['max_parallel']} working at once")
    if m["tests_run"]:
        print(f"  tests         {m['tests_run']} runs · {m['tests_pass_rate']}% passed")
    for key, label in (("projects", "projects"), ("tools", "tools"), ("models", "models"), ("machines", "machines")):
        if m[key]:
            print(f"  {label:<13} " + " · ".join(
                f"{TOOL_NAMES.get(k, k).split(' ·')[0] if key == 'tools' else k} {_hours(v)}" for k, v in m[key]))
    if html:
        path = STATE_DIR / "habits.html"
        path.write_text(habits_html(s), encoding="utf-8")
        import webbrowser
        webbrowser.open(path.as_uri())
        print(f"\nOpened {path}")


def habits_html(s):
    """A private one-page report of your habits (opened locally, never published)."""
    m = s["month"]
    peak = max(s["hours"]) or 1
    cells = "".join(f'<div class="h" style="--a:{v / peak:.2f}" title="{_clock(h)}: {_hours(v)}"></div>'
                    for h, v in enumerate(s["hours"]))
    top = max([d[1] for d in s["daily"]] or [1]) or 1
    bars = "".join(f'<div class="b" style="--v:{v / top:.2f}" title="{d}: {_hours(v)}, {c} commits"></div>'
                   for d, v, c in s["daily"])

    def rows(items, tool=False):
        return "".join(f"<tr><td>{_x(TOOL_NAMES.get(k, k).split(' ·')[0] if tool else k)}</td>"
                       f"<td>{_hours(v)}</td></tr>" for k, v in items) or "<tr><td>—</td></tr>"

    def card(v, label):
        return f'<div class="k"><b>{_x(v)}</b><span>{_x(label)}</span></div>'
    kpis = "".join([
        card(f"{s['streak']} days", f"streak (best {s['best_streak']})"),
        card(_hours(s["week"]["active_minutes"]), "coding, last 7 days"),
        card(_hours(m["active_minutes"]), "coding, last 30 days"),
        card(m["commits"], "commits, 30 days"),
        card(_hours(m["avg_session"]), "average session"),
        card(_hours(m["longest_session"]), "longest session"),
        card(m["max_parallel"], "most agents at once"),
        card(f"{m['tests_pass_rate']}%" if m["tests_pass_rate"] is not None else "—", "tests passed")])
    return f"""<!doctype html><meta charset="utf-8"><title>My coding habits</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{margin:0;background:#0b0d12;color:#e6e8ee;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:900px;margin:0 auto;padding:32px 16px}}h1{{margin:0 0 4px}}p{{color:#9aa4bd;margin:0 0 24px}}
h2{{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#fb923c;margin:28px 0 10px}}
.ks{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}}
.k{{background:#121826;border:1px solid #232c44;border-radius:12px;padding:12px}}.k b{{display:block;font-size:22px}}.k span{{color:#9aa4bd;font-size:13px}}
.hs{{display:grid;grid-template-columns:repeat(24,1fr);gap:3px}}.h{{aspect-ratio:1;border-radius:4px;background:rgba(45,212,191,calc(.08 + var(--a)*.92))}}
.hl{{display:flex;justify-content:space-between;color:#9aa4bd;font-size:12px;margin-top:4px}}
.bs{{display:flex;align-items:flex-end;gap:3px;height:120px}}.b{{flex:1;height:calc(var(--v)*100%);min-height:2px;background:linear-gradient(#f97316,#db2777);border-radius:3px 3px 0 0}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}}
table{{width:100%;border-collapse:collapse}}td{{padding:6px 0;border-bottom:1px solid #232c44}}td:last-child{{text-align:right;color:#9aa4bd}}
</style><main>
<h1>My coding habits</h1><p>Private report from agent-presence, kept only on this computer. Since {_x(s['first_day'])}.</p>
<div class="ks">{kpis}</div>
<h2>When I code (last 30 days)</h2><div class="hs">{cells}</div>
<div class="hl"><span>12 AM</span><span>6 AM</span><span>12 PM</span><span>6 PM</span><span>11 PM</span></div>
<h2>Each day (last 30 days)</h2><div class="bs">{bars}</div>
<div class="cols"><div><h2>Projects</h2><table>{rows(m['projects'])}</table></div>
<div><h2>Tools</h2><table>{rows(m['tools'], True)}</table></div>
<div><h2>Models</h2><table>{rows(m['models'])}</table></div>
<div><h2>Machines</h2><table>{rows(m['machines'])}</table></div></div>
</main>"""


# ------------------------------------------------------------- web ----
# The same activity, with more detail than Discord has room for, published
# to your GitHub so a README card and your website can show it. Two files go
# to a side branch of one of your repos (your profile repo by default):
#   status.json  everything, for a website widget
#   status.svg   an image card for a README
# Each update replaces the branch with a single new commit, so there's no
# history to pile up, and commits off the default branch don't count toward
# your contribution graph.

WEB_BRANCH = "agent-presence-status"
WEB_RAW = "https://raw.githubusercontent.com/{repo}/{branch}/{file}"
WEB_STATE = STATE_DIR / "web.json"
STATE_WORDS = {"vibe-coding": "vibe coding", "coding": "coding", "waiting": "waiting",
               "idle": "not coding right now", "offline": "offline", "summary": "building"}


def web_cfg(cfg):
    return {**DEFAULT_CONFIG["web"], **(cfg.get("web") or {})}


def find_gh():
    import shutil
    hit = shutil.which("gh")
    if hit:
        return hit
    for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh", "/snap/bin/gh",
              os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "GitHub CLI", "gh.exe")):
        if os.path.exists(p):
            return p
    return None


def github_token(cfg):
    """A token that can write the status branch: env var, config, or the GitHub CLI's."""
    tok = os.environ.get("AGENT_PRESENCE_TOKEN") or web_cfg(cfg).get("token")
    if tok:
        return tok.strip()
    gh = find_gh()
    return (run(gh, "auth", "token") or "").strip() or None if gh else None


def github_call(token, method, path, body=None, timeout=15):
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "agent-presence", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return json.loads(data) if data else {}


def publish_files(token, repo, branch, files):
    """Point `branch` at one new parentless commit holding `files`."""
    tree = github_call(token, "POST", f"/repos/{repo}/git/trees", {"tree": [
        {"path": name, "mode": "100644", "type": "blob", "content": content}
        for name, content in files.items()]})
    day = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime())  # the day, not the minute
    who = {"name": "agent-presence", "email": "agent-presence@users.noreply.github.com", "date": day}
    commit = github_call(token, "POST", f"/repos/{repo}/git/commits", {
        "message": "agent-presence status", "tree": tree["sha"], "parents": [],
        "author": who, "committer": who})
    try:
        github_call(token, "PATCH", f"/repos/{repo}/git/refs/heads/{branch}",
                    {"sha": commit["sha"], "force": True})
    except urllib.error.HTTPError as e:
        if e.code not in (404, 422):
            raise
        github_call(token, "POST", f"/repos/{repo}/git/refs",
                    {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})


def strip_file(action):
    """"✍ editing foo.py" -> "✍ editing" (file names stay off the web unless asked)."""
    return re.sub(r"\s+\S*\.[A-Za-z0-9]{1,8}$", "", action)


def is_public_project(s, name, agent=None):
    """Only repos that are public on GitHub get named on the web."""
    root = s["roots"].get(name) if s else None
    slug = github_slug(root) if root else None
    if slug:
        return github_public(slug)
    return bool(agent and agent.get("public"))


def _iso_utc(ts):
    """Absolute UTC timestamp ("2026-10-28T17:04:05Z") for an epoch, or None."""
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _ts_of(value):
    """Epoch seconds for an ISO-8601 UTC string or a number, else None."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            day, clock = value.split("T")
            y, mo, d = (int(x) for x in day.split("-"))
            hh, mm, ss = (int(x) for x in clock.rstrip("Z").split(":"))
        except ValueError:
            return None
        return (date(y, mo, d).toordinal() - date(1970, 1, 1).toordinal()) * 86400 + hh * 3600 + mm * 60 + ss
    return None


def _day_word(ts):
    """"today" / "yesterday" / "Oct 20": never the exact time on the web."""
    if not ts:
        return None
    d = date.fromtimestamp(ts)
    if d == date.today():
        return "today"
    if d == date.today() - timedelta(days=1):
        return "yesterday"
    return d.strftime("%b %d").replace(" 0", " ")


def web_highlights(cfg, s=None):
    """The shareable bits of the private habit log, as [{"value", "label"}]."""
    share = set(web_cfg(cfg)["share"])
    h = habit_summary() if s is None else s
    out = []

    def add(key, value, label):
        if key in share and value:
            out.append({"key": key, "value": value, "label": label})
    add("streak", f"{h['streak']} days" if h["streak"] >= 2 else None, "coding streak")
    add("best_streak", f"{h['best_streak']} days" if h["best_streak"] >= 3 else None, "best streak")
    wk = h["week"]["active_minutes"]
    add("week_hours", f"{round(wk / 60)}h" if wk >= 60 else None, "coding this week")
    add("week_commits", h["week"]["commits"], "commits this week")
    add("month_commits", h["month"]["commits"], "commits, 30 days")
    add("active_days", f"{h['month']['days']}/30" if h["month"]["days"] >= 3 else None, "days coded")
    m = h["month"]
    if m["tests_run"] >= 5:
        add("tests", f"{m['outcomes'].get('tests_pass', 0)}", "tests passed, 30d")
    if web_cfg(cfg)["show_tokens"]:
        add("tokens", human_tokens(h["week"]["tokens"]) if h["week"]["tokens"] else None, "tokens this week")
    return out


def web_status(cfg, live, machines=None, streak=0, state=None, habits=None):
    """What the README card and website widget show. Built to be public:
    private repos are unnamed and OS is major-version only.

    Presence follows docs/presence-truth-model.md: the signals are computed
    independently and published as absolute UTC timestamps. `state` is a
    coding state only with verified human activity or an executing agent —
    an agent that is merely waiting publishes "waiting", and `last_active`
    (the last verified activity) is an ISO timestamp, never "now"."""
    w = web_cfg(cfg)
    hidden = set(cfg["hide_projects"])
    try:
        saved = json.loads(WEB_STATE.read_text())
    except (OSError, ValueError):
        saved = {}
    h = habit_summary() if habits is None else habits
    top = next(((p, v) for p, v in h["week"]["projects"]), None)
    now = time.time()
    out = {"v": 2, "schema_version": 2, "updated": int(now),
           "published_at": _iso_utc(now), "heartbeat_at": _iso_utc(now),
           "tracking_since": h["first_day"],
           "machine_reachable": False, "human_active": "unknown",
           "agent_executing": False, "agent_waiting": False,
           "name": w["name"], "tool": None, "tools": [],
           "verb": None, "title": None, "headline": None, "mood": "idle", "image": None,
           "agents": [], "projects": [], "machines": [], "today": {}, "streak": h["streak"] or streak,
           "model": None, "last_active": _iso_utc(saved.get("last_active")),
           "highlights": web_highlights(cfg, h), "landing": LANDING_URL}
    if top and "top_project" in w["share"] and top[0] in saved.get("public_projects", []):
        out["highlights"].append({"key": "top_project", "value": top[0], "label": "top project, 7 days"})
    if not live or state == "offline" or not w["live"]:
        out["state"] = state or ("idle" if w["live"] else "summary")
        out["updated"] = int(_midnight())  # legacy field: no exact "went offline at" time
        # The publisher itself is alive and reporting (unless it is signing
        # off as offline, or live mode is off and this is only a summary).
        out["machine_reachable"] = bool(w["live"]) and state != "offline"
        if state == "offline":
            out["human_active"] = False
        return out
    primary, s = merge_states(live)
    busy = [a for a in s["agents"] if a["status"] == "busy"]
    waiting = [a for a in s["agents"] if a["status"] == "waiting"]
    out["machine_reachable"] = True
    out["agent_executing"] = bool(busy)
    out["agent_waiting"] = bool(waiting)
    # Human activity needs both halves of the evidence: an agent/editor has
    # focus, and a machine's idle sensor says it was touched recently. With
    # no machine data at all the honest answer is "unknown", never True.
    in_use = any(m.get("in_use") for m in machines or [])
    out["human_active"] = (True if s["focused"] and in_use else
                           "unknown" if not machines and s["focused"] else False)
    if busy:
        out["state"] = "vibe-coding"
    elif out["human_active"] is True:
        out["state"] = "coding"
    elif waiting:
        out["state"] = "waiting"
    else:
        out["state"] = "idle"
    if out["state"] in ("vibe-coding", "coding"):
        # Only verified activity moves last_active; waiting does not.
        saved["last_active"] = now
        out["last_active"] = _iso_utc(now)
    public = {p: p not in hidden and is_public_project(
        s, p, next((a for a in s["agents"] if a.get("project") == p), None)) for p in s["projects"]}

    def name(p, agent=None):
        if not p or p in hidden:
            return "a private project" if p else None
        ok = public.get(p)
        if ok is None:
            ok = is_public_project(s, p, agent)
        return p if ok else "a private project"
    m = mood(s, cfg)
    n_busy, n_wait = len(busy), len(s["agents"]) - len(busy)
    headline = (f"⚡ {plural(n_busy, 'agent')} working" if n_busy else
                f"⏳ {plural(n_wait, 'agent')} waiting" if n_wait else "● hands on keyboard")
    if n_busy and n_wait:
        headline += f" · {n_wait} waiting"
    out.update(tool=primary, tools=[{"id": t, "name": TOOL_NAMES[t].split(" ·")[0]} for t in s["tools"]],
               verb="Watching" if busy and not s["focused"] else "Playing",
               title="Vibe Coding" if pick_name(cfg, s, 2) else "Coding", mood=m,
               image=image(cfg, f"{primary}_{m}", f"{primary}_{MOOD_FALLBACK.get(m, m)}", primary),
               headline=headline)
    for a in sorted(s["agents"], key=lambda a: (a["status"] != "busy", -a["mtime"]))[:8]:
        entry = {
            "tool": a["tool"], "tool_name": TOOL_NAMES[a["tool"]].split(" ·")[0], "status": a["status"],
            "action": a["action"] if w["show_files"] else strip_file(a["action"]),
            "project": name(a.get("project"), a),
            "model": pretty_model(a["model"]) if a.get("model") else None}
        if a.get("machine"):  # provenance for agents on linked machines
            entry["machine"] = a["machine"]
        if a.get("mobile_origin"):  # a job started from a phone, never a phone daemon
            entry["mobile_origin"] = a["mobile_origin"]
        out["agents"].append(entry)
    models = [a["model"] for a in s["agents"] if a.get("model")]
    out["model"] = pretty_model(max(set(models), key=models.count)) if models else None
    for p in s["projects"][:6]:
        if not public.get(p):
            continue  # private repos aren't listed at all
        root = s["roots"].get(p)
        g = git_stats(root) if root else None
        slug = github_slug(root) if root else None
        out["projects"].append({
            "name": p, "branch": g["branch"] if g else None,
            "added": g["added"] if g else 0, "removed": g["removed"] if g else 0,
            "commits_today": g["commits_today"] if g else 0,
            "url": f"https://github.com/{slug}" if slug else None,
            "ci": ci_status(slug, g["branch"]) if slug and g and g["branch"] else None})
    saved["public_projects"] = sorted(set(saved.get("public_projects", [])) | {p for p, ok in public.items() if ok})[-50:]
    if w["show_machines"]:
        out["machines"] = [{"label": x["label"], "icon": x["icon"], "os": x["os"], "agents": x["agents"],
                            **({"last_seen_at": x["last_seen_at"]} if x.get("last_seen_at") else {})}
                           for x in machines or []]
    stats = today_stats()
    out["today"] = {"agent_seconds": int(sum(v[0] for v in stats.values())),
                    "commits": sum(sum(g["commits_today"] for g in [git_stats(r)] if g)
                                   for p, r in s["roots"].items() if p not in hidden)}
    if w["show_tokens"]:
        out["today"]["tokens"] = sum(v[1] for v in stats.values())
    try:
        WEB_STATE.write_text(json.dumps(saved))
    except OSError:
        pass
    return out


def _x(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _when(ts):
    t = time.localtime(ts)
    zone = time.strftime("%Z", t)
    if " " in zone:  # Windows gives "Pacific Daylight Time"
        zone = "".join(w[0] for w in zone.split())
    clock = time.strftime("%I:%M %p", t).lstrip("0")
    day = "" if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d") else time.strftime("%b %d, ", t)
    return f"{day}{clock} {zone}".strip()


def render_svg(st):
    """A README card: dark, 480 px wide, height fits the content. No clock
    times on it: just live, or the day you last coded."""
    W, pad, lh = 480, 20, 22
    rows = []  # (kind, text)
    live = st["state"] in ("vibe-coding", "coding")
    who = st.get("name")
    head = f"{who} is {STATE_WORDS[st['state']]}" if who else STATE_WORDS[st["state"]].capitalize()
    if live:
        sub = " · ".join(filter(None, [st.get("title") and f"{st['verb']} {st['title']}",
                                       ", ".join(t["name"] for t in st["tools"][:3]), st.get("model")]))
    elif _day_word(_ts_of(st.get("last_active"))):
        # last_active is an absolute timestamp in the JSON; the card shows the day.
        sub = f"last coding {_day_word(_ts_of(st['last_active']))}"
    else:
        sub = "Agent Presence"
    if live:
        if st.get("headline"):
            rows.append(("big", st["headline"]))
        for a in [a for a in st["agents"] if a["status"] == "busy"][:3]:
            rows.append(("row", a["action"] + (f" · {a['project']}" if a.get("project") else "")))
        for p in st["projects"][:2]:
            bits = [f"📁 {p['name']}"]
            if p.get("branch"):
                bits.append(f"🌿 {p['branch']}")
            if p.get("added") or p.get("removed"):
                bits.append(f"+{p['added']} −{p['removed']}")
            rows.append(("row", " · ".join(bits)))
        if st["machines"]:
            rows.append(("row", " · ".join(f"{m['icon']} {m['label']} ({m['os']})" for m in st["machines"])))
    hl = [h for h in st.get("highlights") or [] if h["key"] != "top_project"][:6]
    tile_rows = (len(hl) + 2) // 3
    tw, th, gap = (W - 2 * pad - 16) / 3, 46, 8
    H = pad + 50 + len(rows) * lh + (tile_rows * (th + gap) + 12 if hl else 0) + 34
    dot = {"vibe-coding": "#23a55a", "coding": "#23a55a", "waiting": "#f0b232",
           "idle": "#f0b232"}.get(st["state"], "#80848e")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'role="img" aria-label="{_x(head)}">',
           '<style>text{font-family:"Segoe UI",Ubuntu,"Helvetica Neue",Arial,sans-serif;fill:#dbdee1}'
           '.h{font-size:17px;font-weight:700;fill:#f2f3f5}.s{font-size:12.5px;fill:#949ba4}'
           '.big{font-size:14px;font-weight:600;fill:#f2f3f5}.row{font-size:13.5px}.dim{font-size:12.5px;fill:#b5bac1}'
           '.f{font-size:11px;fill:#6d6f78}.v{font-size:15px;font-weight:700;fill:#f2f3f5}.l{font-size:11px;fill:#949ba4}@keyframes p{50%{opacity:.35}}.live{animation:p 2s ease-in-out infinite}</style>',
           f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="#111214" stroke="#2b2d31"/>',
           f'<rect x="1" y="1" width="{W - 2}" height="4" rx="2" fill="url(#g)"/>',
           '<defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#f97316"/>'
           '<stop offset=".5" stop-color="#db2777"/><stop offset="1" stop-color="#14b8a6"/></linearGradient></defs>',
           f'<circle cx="{pad + 6}" cy="{pad + 12}" r="6" fill="{dot}"' + (' class="live"' if live else "") + "/>",
           f'<text class="h" x="{pad + 20}" y="{pad + 18}">{_x(head)}</text>',
           f'<text class="s" x="{pad + 20}" y="{pad + 38}">{_x(clip(sub, 70))}</text>']
    y = pad + 50
    for kind, text in rows:
        y += lh
        out.append(f'<text class="{kind}" x="{pad}" y="{y}">{_x(clip(text, 62))}</text>')
    y += 14 if rows else 4
    for i, h in enumerate(hl):
        x, ty = pad + (i % 3) * (tw + gap), y + (i // 3) * (th + gap)
        out.append(f'<rect x="{x:.1f}" y="{ty}" width="{tw:.1f}" height="{th}" rx="8" fill="#1a1b1e" stroke="#2b2d31"/>'
                   f'<text class="v" x="{x + 10:.1f}" y="{ty + 21}">{_x(clip(str(h["value"]), 14))}</text>'
                   f'<text class="l" x="{x + 10:.1f}" y="{ty + 37}">{_x(clip(h["label"], 22))}</text>')
    out.append(f'<text class="f" x="{pad}" y="{H - 14}">{"live" if live else "updates while I code"}</text>')
    out.append(f'<text class="f" x="{W - pad}" y="{H - 14}" text-anchor="end">🤖 agent-presence</text>')
    out.append("</svg>")
    return "\n".join(out)


def _web_signature(st):
    """What counts as a change worth publishing early (not the clock or counters)."""
    return json.dumps([st["state"], st.get("headline"), [(a["action"], a.get("project")) for a in st["agents"]],
                       [p["name"] for p in st["projects"]], [(m["label"], m["agents"]) for m in st["machines"]],
                       st.get("highlights")])


class WebPublisher:
    """Publishes web_status() when it changes (at most every 30 s) and as a
    heartbeat every `every_seconds`, in the background so the card never waits."""

    def __init__(self, cfg):
        import threading
        self.cfg, self.w = cfg, web_cfg(cfg)
        self.repo, self.branch = self.w["repo"], self.w["branch"] or WEB_BRANCH
        self.last_sig, self.last_at, self.busy = None, 0, threading.Lock()
        self.thread_cls = threading.Thread

    def publish(self, st):
        token = github_token(self.cfg)
        if not token or not self.repo:
            log("web: no GitHub token or repo; run `agent-presence web setup`")
            return False
        try:
            publish_files(token, self.repo, self.branch,
                          {"status.json": json.dumps(st, ensure_ascii=False, indent=1) + "\n",
                           "status.svg": render_svg(st) + "\n"})
            return True
        except Exception as e:
            log("web: publish failed:", repr(e))
            return False

    def update(self, st):
        now, sig = time.time(), _web_signature(st)
        due = now - self.last_at >= max(60, self.w["every_seconds"])
        changed = sig != self.last_sig and now - self.last_at >= 30
        if not (due or changed) or not self.busy.acquire(blocking=False):
            return

        def go():
            try:
                if self.publish(st):
                    self.last_sig, self.last_at = sig, time.time()
                else:
                    self.last_at = time.time()  # back off a full interval after a failure
            finally:
                self.busy.release()
        self.thread_cls(target=go, daemon=True).start()


def web_snippets(repo, branch=WEB_BRANCH, landing=LANDING_URL):
    svg = WEB_RAW.format(repo=repo, branch=branch, file="status.svg")
    md = (f"<!-- agent-presence:start -->\n<a href=\"{landing}\"><img src=\"{svg}\" "
          f"alt=\"What I'm coding right now\" width=\"480\"></a>\n<!-- agent-presence:end -->")
    html = (f'<div data-agent-presence="{repo}"></div>\n'
            f'<script src="{landing}embed.js" async></script>')
    return md, html


# ------------------------------------------------------- Discord IPC ----

class _SocketConn:
    """Discord IPC over a Unix domain socket (Linux, macOS)."""
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(5)
        try:
            self.s.connect(path)
        except OSError:
            self.s.close()
            raise

    def send(self, data):
        self.s.sendall(data)

    def recv(self, n):
        return self.s.recv(n)

    def close(self):
        self.s.close()


class _PipeConn:
    """Discord IPC over a Windows named pipe (\\\\.\\pipe\\discord-ipc-N)."""
    def __init__(self, path):
        self.f = open(path, "r+b", buffering=0)

    def send(self, data):
        self.f.write(data)

    def recv(self, n):
        return self.f.read(n)

    def close(self):
        self.f.close()


def ipc_paths():
    """Every place Discord's IPC endpoint might be (stable, PTB, Canary use 0-9)."""
    if IS_WIN:
        for i in range(10):
            yield rf"\\.\pipe\discord-ipc-{i}"
        return
    bases = [os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")]
    if hasattr(os, "getuid"):
        bases.append(f"/run/user/{os.getuid()}")
    if IS_MAC and not os.environ.get("TMPDIR"):
        bases.append(run("getconf", "DARWIN_USER_TEMP_DIR"))  # launchd may not set TMPDIR
    bases.append("/tmp")
    subs = ["", "snap.discord", "app/com.discordapp.Discord",
            ".flatpak/com.discordapp.Discord/xdg-run"]
    seen = set()
    for b in filter(None, bases):
        for sub in subs:
            for i in range(10):
                path = os.path.join(b, sub, f"discord-ipc-{i}")
                if path not in seen:
                    seen.add(path)
                    yield path


class DiscordIPC:
    MIN_GAP = 4.5  # Discord allows ~5 updates per 20 s per app

    def __init__(self, client_id):
        self.client_id = client_id
        self.sock = None
        self.shown = None
        self.last_sent = 0.0

    def _send(self, op, payload):
        data = json.dumps(payload).encode()
        self.sock.send(struct.pack("<II", op, len(data)) + data)

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("discord closed the connection")
            buf += chunk
        return buf

    def _recv(self):
        op, n = struct.unpack("<II", self._read(8))
        return op, json.loads(self._read(n))

    def connect(self):
        for path in ipc_paths():
            if not IS_WIN and not os.path.exists(path):
                continue
            try:
                self.sock = (_PipeConn if IS_WIN else _SocketConn)(path)
            except OSError:
                continue
            self._send(0, {"v": 1, "client_id": self.client_id})
            _, resp = self._recv()
            if resp.get("evt") != "READY":
                self.close()
                raise ConnectionError(f"handshake failed: {resp}")
            return True
        return False

    def update(self, activity):
        """Set (or clear, with None) this app's card; reconnects as needed."""
        if activity == self.shown or time.time() - self.last_sent < self.MIN_GAP:
            return False
        if not self.sock:
            if activity is None:
                self.shown = None
                return False
            if not self.connect():
                return False
        self._send(1, {"cmd": "SET_ACTIVITY", "nonce": str(uuid.uuid4()),
                       "args": {"pid": os.getpid(), "activity": activity}})
        _, resp = self._recv()
        self.last_sent = time.time()
        if resp.get("evt") == "ERROR":
            log("discord error:", resp.get("data"))
        self.shown = activity
        if activity is None:
            self.close()  # like the real app quitting: card disappears
        return True

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None


# --------------------------------------------------------------- main ----

def merge_states(live):
    """Fold several tools' states into one for the combined card. Returns
    (primary tool, merged state). Primary = the focused tool, else the one
    with the most busy agents, else the most agents, else the newest app."""
    def rank(t):
        st = live[t]
        return (st["focused"], sum(a["status"] == "busy" for a in st["agents"]),
                len(st["agents"]), st["running_since"])
    order = sorted(live, key=rank, reverse=True)
    primary = order[0]
    projects, roots = [], {}
    for t in order:  # the primary tool's projects lead
        for p in live[t]["projects"]:
            if p not in projects:
                projects.append(p)
        roots.update(live[t]["roots"])
    return primary, {"tools": order, "agents": [a for t in order for a in live[t]["agents"]],
                     "projects": projects, "roots": roots,
                     "focused": any(live[t]["focused"] for t in order),
                     "running_since": min(live[t]["running_since"] for t in order)}


def plan_cards(cfg, live, own):
    """card key -> (tool shown, state). Tools with their own Discord app get
    their own card; everything else shares the "combined" card."""
    cards = {t: (t, live[t]) for t in live if t in own}
    rest = {t: st for t, st in live.items() if t not in own}
    if rest:
        cards["combined"] = merge_states(rest)
    return cards


def activities(cfg, tick, live=None, own=None, machines=None, switched=None):
    live = snapshot(cfg) if live is None else live
    own = set(client_ids(cfg)) - {"combined"} if own is None else own
    roots = {r for s in live.values() for p, r in s["roots"].items()
             if p not in cfg["hide_projects"]}
    streak = update_streak(bool(live), roots) if cfg["show_streak"] else 0
    totals = {"agents": sum(len(s["agents"]) for s in live.values()), "tools": len(live)}
    cards = plan_cards(cfg, live, own)
    if machines is None:
        machines = machines_summary(machine_status(cfg, {}), [], cfg)
    return ({k: build_activity(cfg, tool, st, totals, streak, tick, machines, switched)
             for k, (tool, st) in cards.items()}, cards)


PID_FILE = STATE_DIR / "agent-presence.pid"


def already_running():
    """PID of another running copy of this script, if any."""
    try:
        pid = int(PID_FILE.read_text())
    except (OSError, ValueError):
        return None
    if pid == os.getpid():
        return None
    return pid if "ai_presence" in proc_cmdline(pid) else None


def main():
    other = already_running()
    if other:
        sys.exit(f"agent-presence is already running (pid {other}).")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    cfg = load_config()
    ids = client_ids(cfg)
    if not ids:
        sys.exit(f"No Discord applications configured; see client_ids in {CONFIG_PATH}.")
    log("cards enabled for:", ", ".join(
        TOOL_NAMES[t] + (f" ({len(v)} names)" if len(v) > 1 else "") for t, v in ids.items()))
    # One connection per Discord app; an app with several names has several.
    conns = {t: [DiscordIPC(cid) for cid in v] for t, v in ids.items()}
    link = None
    if cfg["link_key"]:
        try:
            link = Link(cfg["link_key"])
            log(f"linked: sharing with your other computers on UDP {LINK_PORT}")
        except OSError as e:
            log("link: can't open the network port, running on this computer only:", e)
    web = WebPublisher(cfg) if web_cfg(cfg)["enabled"] else None
    if web:
        log(f"web: publishing to {web.repo} ({web.branch} branch)")
        import signal

        def stop(*_):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stop)
    relay = None
    if relay_wanted(cfg):
        relay = Relay(cfg["link_key"], cfg)
        log(f"linked: relaying through {relay.repo} for your computers on other networks")
    try:
        _loop(cfg, conns, link, web, relay)
    finally:
        if web and leader_is_me.get("web", True):  # logging out / shutting down
            web.publish(web_status(cfg, {}, state="offline"))


leader_is_me = {}


def _loop(cfg, conns, link, web, relay=None):
    leader, web_leader, switched_at, switched_from = None, None, 0, None
    habits = Habits()
    while True:
        tick = int(time.time() // cfg["rotate_seconds"])
        try:
            live = snapshot(cfg)
            me = machine_status(cfg, live)
            try:  # this computer's own activity only, before other machines' is merged in
                habits.record(cfg, live, me["name"] or me["model"])
            except Exception as e:
                log("habits:", repr(e))
            peers = []
            if link:
                link.send(me)
                peers = link.receive(me["id"])
            if relay:  # computers on other networks; the local packet wins when both arrive
                relay.update(me)
                near = {p["id"] for p in peers}
                peers += [p for p in relay.receive(me["id"]) if p["id"] not in near]
            new_leader = pick_leader(me, peers, leader)
            if new_leader != leader:
                if leader and new_leader == me["id"]:
                    switched_at, switched_from = time.time(), leader
                log("card is on", "this computer" if new_leader == me["id"] else
                    next((p.get("name") or p["model"] for p in peers if p["id"] == new_leader), "?"))
                leader = new_leader
            # One computer publishes the web card: the one you're using, among those
            # set up to publish, even when another one shows the Discord card.
            web_leader = pick_leader(me, peers, web_leader, need="web")
            publish = bool(web) and web_leader == me["id"]
            leader_is_me["v"], leader_is_me["web"] = leader == me["id"], publish
            acts, cards = {}, {}  # unless this computer shows the card
            if leader == me["id"] or publish:
                machines = machines_summary(me, peers, cfg)
                here = next(m["label"] for m in machines if m["me"])
                switched = here if time.time() - switched_at < 120 and switched_from else None
                live = merge_peers(live, peers)
                if leader == me["id"]:
                    acts, cards = activities(cfg, tick, live, set(conns) - {"combined"},
                                             machines, switched)
                if publish:
                    roots = {r for st in live.values() for p, r in st["roots"].items()
                             if p not in cfg["hide_projects"]}
                    web.update(web_status(cfg, live, machines, update_streak(bool(live), roots)
                                          if cfg["show_streak"] else 0))
        except Exception as e:  # never let a parsing hiccup kill the cards
            log("snapshot failed:", repr(e))
            time.sleep(cfg["poll_seconds"])
            continue
        for tool, ipcs in conns.items():
            act = acts.get(tool)
            want = pick_name(cfg, cards[tool][1] if tool in cards else None, len(ipcs))
            # Clear the names that shouldn't show before setting the one that should,
            # so the card never appears twice.
            order = [i for i in range(len(ipcs)) if i != want] + [want]
            for i in order:
                ipc = ipcs[i]
                try:
                    if ipc.update(act if i == want else None) and i == want:
                        shown = f"{TOOL_NAMES[tool]} → {TOOL_NAMES[cards[tool][0]]}" if tool == "combined" else TOOL_NAMES[tool]
                        log(shown + (f" [name {i + 1}]" if len(ipcs) > 1 else "") + ":",
                            f"{act['details']} | {act['state']}" if act else "closed")
                except (OSError, ConnectionError, json.JSONDecodeError) as e:
                    log(f"{TOOL_NAMES[tool]}: discord connection lost:", e)
                    ipc.close()
                    ipc.shown = None
        time.sleep(cfg["poll_seconds"])


# ----------------------------------------------------------------- CLI ----

VERBS = {PLAYING: "Playing", WATCHING: "Watching", COMPETING: "Competing in"}
SERVICE = "ai-presence"
SCRIPT = Path(__file__).resolve()
UNIT_PATH = HOME / f".config/systemd/user/{SERVICE}.service"
PLIST_LABEL = "com.agentpresence"
PLIST_PATH = HOME / f"Library/LaunchAgents/{PLIST_LABEL}.plist"
MAC_LOG = HOME / "Library/Logs/agent-presence.log"
WIN_STARTUP = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming")) / \
    "Microsoft/Windows/Start Menu/Programs/Startup/agent-presence.vbs"
APP_BINARIES = {"claude": ["claude-desktop", "claude"], "chatgpt": ["chatgpt", "codex"],
                "gemini": ["gemini"], "muse": ["muse"], "copilot": ["copilot"],
                "opencode": ["opencode"], "aider": ["aider"], "windsurf": ["windsurf"],
                "kiro": ["kiro"],
                "grok": ["grok", "grok-bot"], "cursor": ["cursor"], "vscode": ["code"]}
# Where the desktop apps install, for `doctor` on macOS / Windows.
MAC_APP_PATHS = {"claude": "Claude.app", "chatgpt": "ChatGPT.app", "cursor": "Cursor.app",
                 "vscode": "Visual Studio Code.app", "windsurf": "Windsurf.app", "kiro": "Kiro.app"}
WIN_APP_PATHS = {"claude": "AnthropicClaude", "cursor": "Programs/cursor",
                 "vscode": "Programs/Microsoft VS Code", "windsurf": "Programs/Windsurf",
                 "kiro": "Programs/Kiro"}


def find_app(tool):
    import shutil
    for b in APP_BINARIES[tool]:
        hit = shutil.which(b)
        if hit:
            return hit
    if IS_MAC and tool in MAC_APP_PATHS:
        for base in (Path("/Applications"), HOME / "Applications"):
            if (base / MAC_APP_PATHS[tool]).exists():
                return str(base / MAC_APP_PATHS[tool])
    if IS_WIN and tool in WIN_APP_PATHS:
        p = Path(os.environ.get("LOCALAPPDATA", "")) / WIN_APP_PATHS[tool]
        if p.exists():
            return str(p)
    return None


def log_hint():
    if IS_LINUX:
        return "journalctl --user -u ai-presence -f"
    if IS_MAC:
        return f"tail -f {MAC_LOG}"
    return f"type {STATE_DIR / 'agent-presence.log'}"


def cmd_detect():
    cfg = load_config()
    live = snapshot(cfg)
    if not live:
        print("No supported apps open and no agents running right now.")
        return
    ids = client_ids(cfg)
    machines = machines_summary(machine_status(cfg, live), [], cfg)
    acts, cards = activities(cfg, 0, live, set(ids) - {"combined"}, machines)
    streak = update_streak(True, {r for st in live.values() for p, r in st["roots"].items()
                                  if p not in cfg["hide_projects"]})
    for key, (tool, s) in cards.items():
        act = acts[key]
        n = len(ids.get(key, []))
        name = (["Coding", "Vibe Coding"][pick_name(cfg, s, 2)] if key == "combined" and n > 1
                else TOOL_NAMES[tool])
        print(f"\n{VERBS[act['type']]} {name}" + ("   (combined card: " + ", ".join(
            TOOL_NAMES[t] for t in s["tools"]) + ")" if key == "combined" else ""))
        print(f"  {act['details']}")
        lines = card_lines(cfg, tool, s, streak, machines)[0]
        print(f"  [big: {tool}_{mood(s, cfg)}]")
        for ln in lines:
            party = ln["party"]
            print(f"  ↻ {ln['text']}" + (f"  ({party[0]} of {party[1]})" if party else "")
                  + f"   [badge: {ln['badge']}]")
        for b in act.get("buttons", []):
            print(f"  [ {b['label']} ] -> {b['url']}")


def cmd_doctor():
    import shutil
    cfg = load_config()
    ok = lambda b: "✓" if b else "✗"
    plat = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"
    print(f"  platform: {plat} · Python {sys.version.split()[0]}")
    if IS_LINUX:
        print(f"{ok(os.environ.get('DISPLAY'))} X11 display: {os.environ.get('DISPLAY') or 'not set'}"
              f" (session: {os.environ.get('XDG_SESSION_TYPE', '?')})")
        print(f"{ok(shutil.which('xprop'))} xprop installed (window detection)")
    else:
        print(f"{ok(psutil)} psutil installed" + ("" if psutil else " — run: pip install psutil"))
    if IS_MAC:
        front = run("osascript", "-e", MAC_SCRIPT)
        print(f"{ok(front)} window detection (System Events)")
        if front and "\t\n" in front.split("\n", 1)[0] + "\n":
            print("  ! no window titles: allow Accessibility for your terminal / Python in"
                  " System Settings → Privacy & Security → Accessibility")
    ipc = None
    for path in ipc_paths():
        if IS_WIN:
            try:
                open(path, "r+b", buffering=0).close()
                ipc = path
                break
            except OSError:
                continue
        elif os.path.exists(path):
            ipc = path
            break
    print(f"{ok(ipc)} Discord running: {ipc or 'no IPC endpoint found (is the desktop app open?)'}")
    ids = client_ids(cfg)
    if "combined" in ids:
        print(f"✓ combined card on ({len(ids['combined'])} names) for every tool without its own app")
    for tool in TOOLS:
        found = find_app(tool)
        card = ("own card" + (f", {len(ids[tool])} names" if len(ids[tool]) > 1 else "")
                if tool in ids else "combined card" if "combined" in ids else "no card")
        where = ("found " + Path(found).name) if found else "not found"
        print(f"{ok(found)} {TOOL_NAMES[tool]:16} {where:24} {card}")
    autostart = UNIT_PATH if IS_LINUX else PLIST_PATH if IS_MAC else WIN_STARTUP
    print(f"{ok(autostart.exists())} starts at login: {autostart}")
    print(f"  config: {CONFIG_PATH}")
    print(f"  log:    {log_hint()}")


def stop_running_copies():
    """Stop other copies of `ai_presence.py run` (Windows has no service manager)."""
    procs, _ = proc_table()
    for pid in procs:
        if pid != os.getpid() and "ai_presence" in (cmd := proc_cmdline(pid)) and "\x00run" in cmd:
            try:
                psutil.Process(pid).terminate() if psutil else os.kill(pid, 15)
            except Exception:
                pass


def cmd_setup(dry_run=False, quiet=False):
    import shutil
    load_config()  # writes a default config if missing
    py = sys.executable
    if IS_LINUX:
        if not shutil.which("systemctl"):
            sys.exit("systemd not found; run `agent-presence run` from your session's autostart instead.")
        target, body = UNIT_PATH, f"""[Unit]
Description=Discord Rich Presence for AI coding tools
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart={py} {SCRIPT} run
Restart=always
RestartSec=20
Environment=DISPLAY={os.environ.get('DISPLAY', ':0')}

[Install]
WantedBy=graphical-session.target
"""
    elif IS_MAC:
        target, body = PLIST_PATH, f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{py}</string><string>{SCRIPT}</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>{MAC_LOG}</string>
  <key>StandardErrorPath</key><string>{MAC_LOG}</string>
</dict>
</plist>
"""
    else:
        pyw = Path(py).with_name("pythonw.exe")
        pyw = str(pyw) if pyw.exists() else py
        target, body = WIN_STARTUP, (
            f'CreateObject("WScript.Shell").Run """{pyw}"" ""{SCRIPT}"" run", 0, False\r\n')
    if dry_run:
        print(f"would write {target}:\n{body}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    if IS_LINUX:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE], check=True)
        subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True)
    elif IS_MAC:
        dom = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", dom, str(target)], capture_output=True)
        if subprocess.run(["launchctl", "bootstrap", dom, str(target)]).returncode:
            subprocess.run(["launchctl", "load", "-w", str(target)], check=True)
    else:
        stop_running_copies()
        subprocess.Popen([pyw, str(SCRIPT), "run"], close_fds=True,
                         creationflags=0x00000008 | 0x08000000)  # detached, no window
    print("Installed and started. It will start on every login.")
    if quiet:  # the macOS installer app shows its own summary
        return
    print()
    cmd_doctor()
    print("\nPreview your cards:  agent-presence detect"
          f"\nLive log:            {log_hint()}")


def cmd_link(key=None, off=False, print_key=False):
    import secrets
    cfg = load_config()
    if print_key:  # just the key, made if needed (for the Mac app)
        if not cfg["link_key"]:
            cfg["link_key"] = secrets.token_urlsafe(12)
            save_config(cfg)
        print(cfg["link_key"])
        return
    if off:
        cfg["link_key"] = ""
        print("Unlinked. This computer shows its own card again.")
    elif key:
        cfg["link_key"] = key.strip()
        print("Linked. Your computers with this key now share one card.")
    elif cfg["link_key"]:
        print(f"This computer's link key:\n\n    {cfg['link_key']}\n\n"
              "On your other computers run:  agent-presence link " + cfg["link_key"])
        return
    else:
        cfg["link_key"] = secrets.token_urlsafe(12)
        print(f"Made a link key for your computers:\n\n    {cfg['link_key']}\n\n"
              "On each of your other computers, run:\n"
              f"    agent-presence link {cfg['link_key']}\n\n"
              "Computers on the same network find each other. On different networks,\n"
              "run `agent-presence web setup` with the same repo on each of them too.\n\n"
              "Keep it private: anyone on your network with it could post to your card.")
    save_config(cfg)
    print("\nRestart to apply:  agent-presence setup")


def cmd_machines(listen=8):
    cfg = load_config()
    me = machine_status(cfg, snapshot(cfg))
    print(f"This computer: {machine_icon(me)} {me['name'] or me['model']}"
          + (f" ({me['chip']})" if me["chip"] else "") + f" · {me['os_full']}")
    idle = me["idle"]
    print(f"  last input: {'unknown' if idle is None else human_duration(idle) + ' ago' if idle >= 60 else 'just now'}"
          f" · Discord {'open' if me['discord'] else 'not running'} · {plural(len(me['agents']), 'agent')}")
    if not cfg["link_key"]:
        print("\nNot linked. Run `agent-presence link` to share one card across your computers.")
        return
    try:
        link = Link(cfg["link_key"])
    except OSError as e:
        print(f"\nCan't open UDP {LINK_PORT}: {e}\n(Is agent-presence already running here? "
              "It holds the port; this still works if your OS allows port sharing.)")
        return
    print(f"\nListening for your other computers for {listen} s…")
    end = time.time() + listen
    while time.time() < end:
        link.send(me)
        time.sleep(1)
        link.receive(me["id"])
    peers = [p for _, p in link.peers.values()]
    if relay_wanted(cfg):
        print(f"Checking {web_cfg(cfg)['repo']} for your computers on other networks…")
        relay, token = Relay(cfg["link_key"], cfg), github_token(cfg)
        try:
            if token:
                relay.sync(me, token)
        except Exception as e:
            print(f"  relay check failed: {e}")
        near = {p["id"] for p in peers}
        peers += [p for p in relay.receive(me["id"]) if p["id"] not in near]
    for p in peers:
        idle = p.get("idle")
        print(f"  {machine_icon(p)} {p.get('name') or p['model']} · {p.get('os_full', '')} · "
              f"{'input ' + ('just now' if idle < 60 else human_duration(idle) + ' ago') if idle is not None else 'input unknown'}"
              f" · Discord {'open' if p.get('discord') else 'closed'} · {plural(len(p.get('agents', [])), 'agent')}")
    if not peers:
        print("  none found. Check the other computer is linked with the same key, on the same"
              " network, and that its firewall allows UDP 47777 (or, on another network,"
              " that both ran `agent-presence web setup` with the same repo).")

    def name(mid):
        return "this computer" if mid == me["id"] else \
            next(p.get("name") or p["model"] for p in peers if p["id"] == mid)
    print("\nThe card shows on: " + name(pick_leader(me, peers)))
    if any(p.get("web") for p in [me] + peers):
        print("The web card is published by: " + name(pick_leader(me, peers, need="web")))


def cmd_web(action, repo=None, name=None, token=None, readme=False, yes=False):
    cfg = load_config()
    w = web_cfg(cfg)
    if action == "off":
        cfg["web"] = {**w, "enabled": False}
        save_config(cfg)
        print("Stopped publishing. Restart to apply:  agent-presence setup\n"
              f"(The last status stays on the {w['branch']} branch until you delete it.)")
        return
    if action == "preview":
        live = snapshot(cfg)
        st = web_status(cfg, live, machines_summary(machine_status(cfg, live), [], cfg))
        Path("status.json").write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n")
        Path("status.svg").write_text(render_svg(st) + "\n")
        print("Wrote status.json and status.svg here. Open status.svg in a browser to see the card.")
        return
    if action == "status":
        print(f"publishing: {'on' if w['enabled'] else 'off'}" + (f" → {w['repo']} ({w['branch']})" if w["repo"] else ""))
        if w["repo"]:
            md, html = web_snippets(w["repo"], w["branch"])
            print(f"\nREADME:\n{md}\n\nWebsite:\n{html}")
        return

    # setup
    if token:
        cfg["web"] = {**w, "token": token}
    elif not (os.environ.get("AGENT_PRESENCE_TOKEN") or w.get("token")) and find_gh() and not yes:
        print("Safer option: a fine-grained token that can only write your profile repo.\n"
              "  1. https://github.com/settings/personal-access-tokens/new\n"
              "  2. Repository access: only <you>/<you>. Permissions: Contents → Read and write.\n"
              "  3. agent-presence web setup --token <token>\n"
              "Your GitHub CLI login works too, but it can write to all your repos, and this\n"
              "runs in the background all day.")
        if input("Use your GitHub CLI login anyway? [y/N] ").strip().lower() not in ("y", "yes"):
            return
    elif not (os.environ.get("AGENT_PRESENCE_TOKEN") or w.get("token")) and find_gh():
        print("Using your GitHub CLI login, which can write to all your repos. A fine-grained\n"
              "token limited to one repo is safer: agent-presence web setup --token <token>")
    tok = github_token(cfg)
    if not tok:
        sys.exit("No GitHub login found. Either install the GitHub CLI and run `gh auth login`,\n"
                 "or make a fine-grained token (github.com/settings/personal-access-tokens) with\n"
                 "Contents: Read and write on your profile repo, then run:\n"
                 "    agent-presence web setup --token <token>")
    try:
        login = github_call(tok, "GET", "/user")["login"]
    except Exception as e:
        sys.exit(f"GitHub didn't accept the token: {e}")
    repo = repo or w["repo"] or f"{login}/{login}"
    try:
        github_call(tok, "GET", f"/repos/{repo}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit(f"Can't find {repo}. Create your profile repo (a public repo named {login})\n"
                     f"at https://github.com/new, or pick another: agent-presence web setup --repo owner/name")
        raise
    cfg["web"] = {**cfg["web"], "enabled": True, "repo": repo, "name": name or w["name"] or login}
    live = snapshot(cfg)
    st = web_status(cfg, live, machines_summary(machine_status(cfg, live), [], cfg))
    if not WebPublisher(cfg).publish(st):
        sys.exit("Couldn't publish. The token needs permission to write to " + repo + ".")
    save_config(cfg)
    branch = cfg["web"]["branch"]
    md, html = web_snippets(repo, branch)
    print(f"Publishing to {repo} on the {branch} branch (one commit, replaced on each update;\n"
          "it doesn't count toward your contribution graph).\n")
    print(f"README card:\n\n{md}\n\nAny website:\n\n{html}\n\n"
          f"Raw data: {WEB_RAW.format(repo=repo, branch=branch, file='status.json')}\n")
    if readme:
        add_to_readme(tok, f"{login}/{login}" if not repo else repo, md, yes)
    else:
        print(f"Add the card to your GitHub profile automatically:  agent-presence web setup --readme")
    print("Restart to start publishing:  agent-presence setup")


def add_to_readme(token, repo, snippet, yes=False):
    """Put the card into repo's README, between agent-presence markers (one commit)."""
    import base64
    try:
        cur = github_call(token, "GET", f"/repos/{repo}/contents/README.md")
        text = base64.b64decode(cur["content"]).decode("utf-8")
        sha = cur["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        text, sha = "", None
    new = insert_snippet(text, snippet)
    if new == text:
        print(f"{repo} README already has the card.")
        return
    if not yes:
        ans = input(f"Add the card to the README of {repo}? This makes one commit. [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("Skipped. Paste the snippet above wherever you like.")
            return
    body = {"message": "Add live coding status card (agent-presence)",
            "content": base64.b64encode(new.encode("utf-8")).decode()}
    if sha:
        body["sha"] = sha
    github_call(token, "PUT", f"/repos/{repo}/contents/README.md", body)
    print(f"Added to https://github.com/{repo}")


def insert_snippet(text, snippet):
    """Replace an existing agent-presence block, or add one under the first heading."""
    block = re.compile(r"<!-- agent-presence:start -->.*?<!-- agent-presence:end -->", re.S)
    if block.search(text):
        return block.sub(lambda _: snippet, text, count=1)
    lines = text.splitlines(keepends=True)
    at = next((i + 1 for i, ln in enumerate(lines) if ln.lstrip().startswith(("# ", "<h1"))), 0)
    lines[at:at] = ["\n" if at else "", snippet + "\n", "\n" if not at else ""]
    return "".join(lines)


def cmd_uninstall():
    if IS_LINUX:
        subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE])
        UNIT_PATH.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"])
    elif IS_MAC:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)])
        PLIST_PATH.unlink(missing_ok=True)
    else:
        WIN_STARTUP.unlink(missing_ok=True)
        stop_running_copies()
    print(f"Removed. Config left at {CONFIG_PATH}.")


def cli():
    import argparse
    ap = argparse.ArgumentParser(prog="agent-presence",
                                 description="Discord Rich Presence for AI coding tools.")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run in the foreground (what the login service runs)")
    sub.add_parser("detect", help="preview every card and rotating line")
    sp = sub.add_parser("setup", help="install and start the login service")
    sp.add_argument("--dry-run", action="store_true", help="print what would be installed")
    sp.add_argument("--quiet", action="store_true", help="skip the doctor report afterwards")
    sub.add_parser("uninstall", help="stop and remove the login service")
    sub.add_parser("doctor", help="check Discord, window detection and which apps are found")
    lp = sub.add_parser("link", help="share one card across your computers")
    lp.add_argument("key", nargs="?", help="the key from your first computer")
    lp.add_argument("--off", action="store_true", help="stop sharing")
    lp.add_argument("--print-key", action="store_true", help="print only the key (made if needed)")
    sub.add_parser("machines", help="show this computer and your linked ones")
    stp = sub.add_parser("stats", help="your coding habits (private, from this computer)")
    stp.add_argument("--html", action="store_true", help="open a private report in your browser")
    wp = sub.add_parser("web", help="show your activity on your GitHub README and website")
    wp.add_argument("action", nargs="?", default="setup", choices=["setup", "status", "preview", "off"])
    wp.add_argument("--repo", help="owner/name to publish to (default: your profile repo)")
    wp.add_argument("--name", help="name on the card (default: your GitHub login)")
    wp.add_argument("--token", help="a GitHub token, if you don't use the GitHub CLI")
    wp.add_argument("--token-file", help="read the token from this file, then delete it "
                    "(keeps it out of the process list)")
    wp.add_argument("--readme", action="store_true", help="also add the card to your profile README")
    wp.add_argument("-y", "--yes", action="store_true",
                    help="don't ask: use the GitHub CLI login, and edit the README if --readme")
    args, _ = ap.parse_known_args(["detect" if a == "--detect" else a for a in sys.argv[1:]])
    if args.cmd == "setup":
        return cmd_setup(args.dry_run, args.quiet)
    if args.cmd == "link":
        return cmd_link(args.key, args.off, args.print_key)
    if args.cmd == "stats":
        return cmd_stats(args.html)
    if args.cmd == "web":
        token = args.token
        if args.token_file:
            token = Path(args.token_file).read_text().strip()
            Path(args.token_file).unlink()
        return cmd_web(args.action, args.repo, args.name, token, args.readme, args.yes)
    {"detect": cmd_detect, "uninstall": cmd_uninstall, "machines": cmd_machines,
     "doctor": cmd_doctor}.get(args.cmd, main)()


if __name__ == "__main__":
    if IS_WIN:  # emoji in card text
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
    cli()
