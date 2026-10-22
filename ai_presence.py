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

HOME = Path.home()
CONFIG_PATH = HOME / ".config/ai-presence/config.json"
STATE_DIR = HOME / ".local/state/ai-presence"
DEFAULT_CONFIG = {
    # Application ID per app, from discord.com/developers/applications.
    # Name each Discord application what you want after "Playing".
    # Apps left blank are skipped.
    "client_ids": {"claude": "", "chatgpt": "", "grok": "", "cursor": "", "vscode": ""},
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
    "mood_verbs": True,       # Watching / Competing in, instead of always Playing
    # Images: "assets" = keys uploaded to each Discord app's Art Assets,
    # "url" = PNGs hosted at image_base_url, "none" = no images.
    "images": "none",
    "image_base_url": "",
}

TOOLS = ["claude", "chatgpt", "grok", "cursor", "vscode"]
TOOL_NAMES = {"claude": "Claude", "chatgpt": "ChatGPT · Codex", "grok": "Grok Build",
              "cursor": "Cursor", "vscode": "VS Code"}
# Main app process names (/proc/<pid>/comm) -> tool
APP_PROCS = {"claude-desktop": "claude", "ChatGPT": "chatgpt", "grok-bot": "grok",
             "cursor": "cursor", "code": "vscode"}
# CLI agent process names -> tool
CLI_AGENTS = {"claude": "claude", "codex": "chatgpt", "grok": "grok"}
# WM_CLASS (lowercased, snap suffix stripped) -> tool
WINDOW_APPS = {"com.anthropic.claude": "claude", "chatgpt": "chatgpt", "grok-bot": "grok",
               "cursor": "cursor", "code": "vscode"}
# Claude Code transcript "entrypoint" -> tool that hosts the agent
CLAUDE_ENTRYPOINTS = {"claude-vscode": "vscode", "claude-cursor": "cursor"}
TERMINALS = {"gnome-terminal-server", "gnome-terminal", "kitty", "alacritty",
             "konsole", "xterm", "wezterm", "tilix", "terminator", "ghostty"}
# Tools that mean the agent has stopped to ask you something.
ASKING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}

SEP = " ✦ "
CLK_TCK = os.sysconf("SC_CLK_TCK")
# Discord activity types
PLAYING, WATCHING, COMPETING = 0, 3, 5


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text())
        for old in ("client_id", "linger_seconds", "include_vscode", "use_images"):
            user.pop(old, None)  # from earlier versions
        cfg.update(user)
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


# ---------------------------------------------------------------- X11 ----

def window_info(wid):
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
    return {"cls": cls, "pid": pid, "title": title}


def active_window():
    out = run("xprop", "-root", "_NET_ACTIVE_WINDOW")
    wid = out.split()[-1] if out else ""
    if not wid.startswith("0x") or wid == "0x0":
        return None
    return window_info(wid)


def all_windows():
    out = run("xprop", "-root", "_NET_CLIENT_LIST")
    return [window_info(w.strip(",")) for w in out.split() if w.startswith("0x")]


def window_tool(win):
    for key, tool in WINDOW_APPS.items():
        if key in win["cls"]:
            return tool
    return None


# ------------------------------------------------------------ processes ----

def proc_table():
    """{pid: (comm, ppid, start_epoch)} plus a children map."""
    boot = time.time() - float(Path("/proc/uptime").read_text().split()[0])
    procs, kids = {}, {}
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
        pid = int(d.name)
        procs[pid] = (comm, ppid, start)
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
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


# ------------------------------------------------------------ projects ----

KNOWN_ROOTS = {}  # project name -> repo root, learned from agent cwds


def resolve_project(cwd):
    """cwd -> (project name, repo root) or (None, None)."""
    if not cwd:
        return None, None
    # Desktop "chat" scratch dirs aren't projects.
    if "/.config/Claude/scratch-workspaces/" in cwd or "/Documents/Codex/" in cwd:
        return None, None
    p = Path(cwd)
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
    candidates = [HOME / d / name for d in ("Desktop", "", "Documents", "projects", "code", "src")]
    candidates += list((HOME / "Desktop").glob(f"*/{name}"))
    for c in candidates:
        if (c / ".git").exists():
            KNOWN_ROOTS[name] = str(c)
            return str(c)
    return None


def editor_project(title):
    # "file.py — project — Cursor"  /  "project — Visual Studio Code"
    parts = [s.strip() for s in title.replace(" - ", " — ").split(" — ")]
    if len(parts) < 2:
        return None
    name = parts[-2]
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
    return info


def gather_agents(cfg):
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
    for pid, (comm, ppid, start) in procs.items():
        tool = APP_PROCS.get(comm)
        if tool and procs.get(ppid, ("",))[0] != comm:
            rs = state[tool]["running_since"]
            state[tool]["running_since"] = min(rs, start) if rs else start

    agents = gather_agents(cfg)
    # Grok Build has no transcript we can read; a running CLI process is an agent.
    for pid, (comm, ppid, start) in procs.items():
        if comm == "grok":
            agents.append({"tool": "grok", "mtime": now, "model": None,
                           "cwd": proc_cwd(pid), "branch": None,
                           "status": "busy", "action": "⚡ working"})
            rs = state["grok"]["running_since"]
            state["grok"]["running_since"] = min(rs, start) if rs else start
    for a in agents:
        a["project"], root = resolve_project(a["cwd"])
        state[a["tool"]]["agents"].append(a)
        add_project(a["tool"], a["project"], root, 2)  # agents rank above open windows

    for w in all_windows():
        tool = window_tool(w)
        if tool in ("cursor", "vscode"):
            name = editor_project(w["title"])
            add_project(tool, name, root_for_name(name) if name else None)

    win = active_window()
    if win:
        tool = window_tool(win)
        if tool is None and set(win["cls"]) & TERMINALS and win["pid"]:
            hits = [(procs[c][2], c) for c in descendants(kids, win["pid"])
                    if procs.get(c, ("",))[0] in CLI_AGENTS]
            if hits:
                tool = CLI_AGENTS[procs[max(hits)[1]][0]]
        if tool:
            state[tool]["focused"] = True
            if tool in ("cursor", "vscode"):
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


def image(cfg, key):
    if cfg["images"] == "url" and cfg["image_base_url"]:
        return f"{cfg['image_base_url'].rstrip('/')}/{key}.png"
    return key


def card_lines(cfg, tool, s, streak):
    """Every line this card can rotate through, as (text, party) pairs."""
    hidden = set(cfg["hide_projects"])
    agents = s["agents"]
    busy = [a for a in agents if a["status"] == "busy"]
    waiting = [a for a in agents if a["status"] == "waiting"]
    lines = []

    # 1. Agent headline, with Discord's "(2 of 3)" party counter.
    if agents:
        if busy and waiting:
            text = f"⚡ {len(busy)} cooking · ⏳ {len(waiting)} waiting for me"
        elif busy:
            text = f"⚡ {plural(len(busy), 'agent')} cooking"
        else:
            text = f"⏳ {plural(len(waiting), 'agent')} waiting for me"
        lines.append((text, [len(busy), len(agents)] if len(agents) > 1 else None))
    elif s["focused"]:
        lines.append(("● hands on keyboard", None))
    else:
        lines.append(("○ open in the background", None))

    # 2. What each busy agent is doing right now (newest first, up to 3).
    for a in sorted(busy, key=lambda a: -a["mtime"])[:3]:
        where = a["project"] if a["project"] and a["project"] not in hidden else None
        lines.append((f"{a['action']} · {where}" if where and cfg["show_project"] else a["action"], None))

    # 3. Git for the top project, then streak / commits, then model + CI.
    top = next((p for p in s["projects"] if p not in hidden and s["roots"].get(p)), None)
    root = s["roots"].get(top) if top else None
    git = git_stats(root) if root else None
    if cfg["show_git"] and git and git["branch"]:
        diff = f" · +{git['added']} −{git['removed']}" if git["added"] or git["removed"] else " · clean"
        lines.append((f"🌿 {git['branch']}{diff}", None))

    bits = []
    if cfg["show_streak"] and streak >= 2:
        bits.append(f"🔥 {streak}-day streak")
    commits = sum(git_stats(r)["commits_today"] for p, r in s["roots"].items() if p not in hidden)
    if commits:
        bits.append(f"📦 {plural(commits, 'commit')} today")
    if bits:
        lines.append((" · ".join(bits), None))

    bits = []
    models = [pretty_model(a["model"]) for a in agents if a["model"]]
    if cfg["show_model"] and models:
        bits.append(f"🧠 {max(set(models), key=models.count)}")
    slug = github_slug(root) if root else None
    if cfg["show_ci"] and slug and git and git["branch"] and github_public(slug):
        ci = ci_status(slug, git["branch"])
        if ci:
            bits.append(ci)
    if bits:
        lines.append((" · ".join(bits), None))
    return [(clip(t), p) for t, p in lines], top, slug


def build_activity(cfg, tool, s, totals, streak, tick):
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

    lines, top, slug = card_lines(cfg, tool, s, streak)
    text, party = lines[tick % len(lines)]

    kind = PLAYING
    if cfg["mood_verbs"]:
        if len(agents) >= 5:
            kind = COMPETING          # "Competing in Claude"
        elif busy and not s["focused"]:
            kind = WATCHING           # "Watching Claude" while it cooks

    act = {"type": kind, "details": details, "state": text,
           "timestamps": {"start": int(s["running_since"])}}
    if party:
        act["party"] = {"id": f"ai-presence-{tool}", "size": party}

    if cfg["github_button"] and slug and top and github_public(slug):
        label = f"⭐ {top} on GitHub"
        act["buttons"] = [{"label": label if len(label) <= 32 else "⭐ View on GitHub",
                           "url": f"https://github.com/{slug}"}]

    if cfg["images"] in ("assets", "url"):
        across = (f"{plural(totals['agents'], 'agent')} across {plural(totals['tools'], 'app')}"
                  if totals["tools"] > 1 else TOOL_NAMES[tool])
        act["assets"] = {"large_image": image(cfg, tool if cfg["images"] == "url" else "logo"),
                         "large_text": across}
        if agents:
            models = [pretty_model(a["model"]) for a in agents if a["model"]]
            act["assets"]["small_image"] = image(cfg, f"agents_{min(len(agents), 9)}")
            act["assets"]["small_text"] = (f"{plural(len(agents), 'agent')}"
                                           + (f" · {models[0]}" if models else ""))
    return act


# ------------------------------------------------------- Discord IPC ----

class DiscordIPC:
    MIN_GAP = 4.5  # Discord allows ~5 updates per 20 s per app

    def __init__(self, client_id):
        self.client_id = client_id
        self.sock = None
        self.shown = None
        self.last_sent = 0.0

    def _paths(self):
        bases = [os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "/tmp"]
        subs = ["", "snap.discord/", "app/com.discordapp.Discord/",
                ".flatpak/com.discordapp.Discord/xdg-run/"]
        for b in bases:
            for s in subs:
                for i in range(10):
                    yield f"{b}/{s}discord-ipc-{i}"

    def _send(self, op, payload):
        data = json.dumps(payload).encode()
        self.sock.sendall(struct.pack("<II", op, len(data)) + data)

    def _recv(self):
        hdr = self.sock.recv(8)
        if len(hdr) < 8:
            raise ConnectionError("discord closed socket")
        op, n = struct.unpack("<II", hdr)
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("discord closed socket")
            buf += chunk
        return op, json.loads(buf)

    def connect(self):
        for path in self._paths():
            if not os.path.exists(path):
                continue
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            try:
                s.connect(path)
            except OSError:
                s.close()
                continue
            self.sock = s
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

def activities(cfg, tick):
    live = snapshot(cfg)
    roots = {r for s in live.values() for p, r in s["roots"].items()
             if p not in cfg["hide_projects"]}
    streak = update_streak(bool(live), roots) if cfg["show_streak"] else 0
    totals = {"agents": sum(len(s["agents"]) for s in live.values()), "tools": len(live)}
    return {t: build_activity(cfg, t, s, totals, streak, tick) for t, s in live.items()}


def main():
    cfg = load_config()
    ids = {t: cid for t, cid in cfg["client_ids"].items() if cid}
    if not ids:
        sys.exit(f"Add at least one Application ID under client_ids in {CONFIG_PATH} "
                 "(see README.md).")
    log("cards enabled for:", ", ".join(TOOL_NAMES[t] for t in ids))
    conns = {t: DiscordIPC(cid) for t, cid in ids.items()}
    while True:
        tick = int(time.time() // cfg["rotate_seconds"])
        try:
            acts = activities(cfg, tick)
        except Exception as e:  # never let a parsing hiccup kill the cards
            log("snapshot failed:", repr(e))
            time.sleep(cfg["poll_seconds"])
            continue
        for tool, ipc in conns.items():
            try:
                if ipc.update(acts.get(tool)):
                    a = acts.get(tool)
                    log(f"{TOOL_NAMES[tool]}:", f"{a['details']} | {a['state']}" if a else "closed")
            except (OSError, ConnectionError, json.JSONDecodeError) as e:
                log(f"{TOOL_NAMES[tool]}: discord connection lost:", e)
                ipc.close()
                ipc.shown = None
        time.sleep(cfg["poll_seconds"])


VERBS = {PLAYING: "Playing", WATCHING: "Watching", COMPETING: "Competing in"}

if __name__ == "__main__":
    if "--detect" in sys.argv:
        cfg = load_config()
        live = snapshot(cfg)
        roots = {r for s in live.values() for r in s["roots"].values()}
        streak = update_streak(bool(live), roots)
        totals = {"agents": sum(len(s["agents"]) for s in live.values()), "tools": len(live)}
        for tool, s in live.items():
            act = build_activity(cfg, tool, s, totals, streak, 0)
            print(f"\n{VERBS[act['type']]} {TOOL_NAMES[tool]}")
            print(f"  {act['details']}")
            for text, party in card_lines(cfg, tool, s, streak)[0]:
                print(f"  ↻ {text}" + (f"  ({party[0]} of {party[1]})" if party else ""))
            for b in act.get("buttons", []):
                print(f"  [ {b['label']} ] -> {b['url']}")
        sys.exit(0)
    main()
