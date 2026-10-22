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
    # Project names listed here are shown as "private project", and get
    # no git/CI lines or GitHub button.
    "hide_projects": [],
    "show_project": True,
    "show_files": True,       # "editing foo.py" vs just "editing"
    "show_git": True,         # branch and +/- lines
    "show_streak": True,
    "show_model": True,
    "show_ci": True,          # public GitHub repos only
    "github_button": True,    # public GitHub repos only
    "promo_button": True,     # 2nd button: "Get Agent Presence" -> landing page
    "show_stats": True,       # "⏱ 3h 40m of agent time today · 💸 1.2M tokens"
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


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text())
        for old in ("client_id", "linger_seconds", "include_vscode", "use_images"):
            user.pop(old, None)  # from earlier versions
        cfg.update(user)
        cfg["client_ids"] = {**DEFAULT_CONFIG["client_ids"], **user.get("client_ids", {})}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    return cfg


def cached(store, key, ttl, fn):
    hit = store.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    store[key] = (time.time(), val)
    return val


def run(*cmd, cwd=None):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=5).stdout.strip()
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
    if p == HOME:
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


def action_for(tool, args, show_files):
    """Turn an agent's latest tool call into a short, friendly line."""
    args = args if isinstance(args, dict) else {}
    fname = Path(str(args.get("file_path") or args.get("notebook_path") or "")).name
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


def gather_agents(cfg, procs=None):
    now = time.time()
    keep = now - max(cfg["agent_active_seconds"], cfg["agent_waiting_seconds"])
    agents = []

    def wanted(a):
        age = now - a["mtime"]
        return age < (cfg["agent_waiting_seconds"] if a["status"] == "waiting"
                      else cfg["agent_active_seconds"])

    for base in HOME.glob(".claude*/projects"):
        for f in base.glob("*/*.jsonl"):
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if m >= keep:
                a = claude_agent(f, m, cfg)
                if wanted(a):
                    agents.append(a)
    sessions = HOME / ".codex/sessions"
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
                        agents.append(a)

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
        for base in HOME.glob(".claude*/projects"):
            for f in base.glob("*/*.jsonl"):
                try:
                    if f.stat().st_mtime >= midnight:
                        _scan(f, "claude", "claude", midnight)
                except OSError:
                    pass
        sessions = HOME / ".codex/sessions"
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


def card_lines(cfg, tool, s, streak, machines=None, switched=None):
    """Every line this card can rotate through, as dicts of
    text / party ("(2 of 3)") / badge (small image) / hover (its tooltip)."""
    hidden = set(cfg["hide_projects"])
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
    for a in sorted(busy, key=lambda a: -a["mtime"])[:3]:
        where = a["project"] if a["project"] and a["project"] not in hidden else None
        text = f"{a['action']} · {where}" if where and cfg["show_project"] else a["action"]
        add(text, ACTION_BADGES.get(a["action"].split()[0]), a["action"])

    # 3. Result of the newest test/build run (within the last 30 minutes).
    o = latest_outcome(s)
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
    top = next((p for p in s["projects"] if p not in hidden and s["roots"].get(p)), None)
    root = s["roots"].get(top) if top else None
    git = git_stats(root) if root else None
    if cfg["show_git"] and git and git["branch"]:
        diff = f" · +{git['added']} −{git['removed']}" if git["added"] or git["removed"] else " · clean"
        add(f"🌿 {git['branch']}{diff}", "git", f"{top} on {git['branch']}")

    bits = []
    if cfg["show_streak"] and streak >= 2:
        bits.append(f"🔥 {streak}-day streak")
    commits = sum(git_stats(r)["commits_today"] for p, r in s["roots"].items() if p not in hidden)
    if commits:
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
    projects = list(dict.fromkeys(
        "private project" if p in cfg["hide_projects"] else p for p in s["projects"]))
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
# status packet over the local network; the one you used most recently
# shows the card, with every machine's agents on it.

MAC_OS_NAMES = {26: "Tahoe", 15: "Sequoia", 14: "Sonoma", 13: "Ventura", 12: "Monterey", 11: "Big Sur"}
JUNK_MODELS = {"", "to be filled by o.e.m.", "system product name", "default string",
               "not applicable", "none", "o.e.m.", "type1productconfigid"}
LAPTOP_CHASSIS = {"8", "9", "10", "14", "30", "31", "32"}  # DMI chassis types
LINK_PORT = 47777
PEER_TTL = 20            # seconds a peer stays known without a packet
IN_USE_SECONDS = 120     # touched this recently = "in use"
SWITCH_MARGIN = 15       # hysteresis before the card moves to another machine
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
            hw = json.loads(run("system_profiler", "-json", "SPHardwareDataType") or "{}")
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
                  "\"$($c.Manufacturer)|$($c.Model)|$($c.PCSystemType)\"")
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
            agents.append({"tool": tool, "status": a["status"], "action": a["action"],
                           "project": None if proj in hidden else proj,
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
            "agents": agents[:12], "tools": tools}


class Link:
    """Shares machine_status() with your other computers over UDP broadcast.
    Packets are signed with the link key, so only your machines are trusted."""

    def __init__(self, key, port=LINK_PORT, target="<broadcast>"):
        import hashlib
        self.key = hashlib.sha256(key.encode()).digest()
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
        import hmac
        body = json.dumps({**packet, "t": round(time.time(), 1)}, separators=(",", ":")).encode()
        return hmac.new(self.key, body, "sha256").digest()[:16] + body

    def open(self, data):
        import hmac
        mac, body = data[:16], data[16:]
        if not hmac.compare_digest(mac, hmac.new(self.key, body, "sha256").digest()[:16]):
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


def pick_leader(me, peers, current=None):
    """ID of the machine that should show the card: the one you touched most
    recently, among machines with Discord open. Sticks with the current one
    until another is clearly more recent, so it doesn't flicker."""
    every = [me] + list(peers)
    able = [p for p in every if p.get("discord")] or [me]

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
                    "in_use": in_use, "me": p is me})
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
    leader, switched_at, switched_from = None, 0, None
    while True:
        tick = int(time.time() // cfg["rotate_seconds"])
        try:
            live = snapshot(cfg)
            me = machine_status(cfg, live)
            peers = []
            if link:
                link.send(me)
                peers = link.receive(me["id"])
            new_leader = pick_leader(me, peers, leader)
            if new_leader != leader:
                if leader and new_leader == me["id"]:
                    switched_at, switched_from = time.time(), leader
                log("card is on", "this computer" if new_leader == me["id"] else
                    next((p.get("name") or p["model"] for p in peers if p["id"] == new_leader), "?"))
                leader = new_leader
            if leader != me["id"]:
                acts, cards = {}, {}  # another computer shows the card
            else:
                machines = machines_summary(me, peers, cfg)
                here = next(m["label"] for m in machines if m["me"])
                switched = here if time.time() - switched_at < 120 and switched_from else None
                live = merge_peers(live, peers)
                acts, cards = activities(cfg, tick, live, set(conns) - {"combined"},
                                         machines, switched)
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


def cmd_link(key=None, off=False):
    import secrets
    cfg = load_config()
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
              "On each of your other computers (same Wi-Fi or network), run:\n"
              f"    agent-presence link {cfg['link_key']}\n\n"
              "Keep it private: anyone on your network with it could post to your card.")
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
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
    for p in peers:
        idle = p.get("idle")
        print(f"  {machine_icon(p)} {p.get('name') or p['model']} · {p.get('os_full', '')} · "
              f"{'input ' + ('just now' if idle < 60 else human_duration(idle) + ' ago') if idle is not None else 'input unknown'}"
              f" · Discord {'open' if p.get('discord') else 'closed'} · {plural(len(p.get('agents', [])), 'agent')}")
    if not peers:
        print("  none found. Check the other computer is linked with the same key, on the same"
              " network, and that its firewall allows UDP 47777.")
    lead = pick_leader(me, peers)
    print("\nThe card shows on: " + ("this computer" if lead == me["id"] else
                                  next(p.get("name") or p["model"] for p in peers if p["id"] == lead)))


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
    sub.add_parser("machines", help="show this computer and your linked ones")
    args, _ = ap.parse_known_args(["detect" if a == "--detect" else a for a in sys.argv[1:]])
    if args.cmd == "setup":
        return cmd_setup(args.dry_run, args.quiet)
    if args.cmd == "link":
        return cmd_link(args.key, args.off)
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
