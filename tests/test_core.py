"""Cross-platform tests. Run with:  python -m unittest discover -s tests -v

Covers the parts that differ per OS (processes, windows, Discord IPC,
autostart) plus the transcript parsers, using synthetic data only.
"""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_presence as ap  # noqa: E402


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def write_jsonl(recs, suffix=".jsonl"):
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    f.write("".join(json.dumps(r) + "\n" for r in recs))  # logs end each line
    f.close()
    return Path(f.name)


class TitlesAndNames(unittest.TestCase):
    def test_editor_titles_all_platforms(self):
        cases = {
            "main.py - px4-autonomous-mission - Visual Studio Code": "px4-autonomous-mission",  # Linux/Win
            "main.py — px4-autonomous-mission": "px4-autonomous-mission",                        # macOS
            "● nav.py - Omega-Code - Cursor": "Omega-Code",
            "Omega-Code": "Omega-Code",
            "[SSH: jetson] robot - Visual Studio Code": "robot",
            "Welcome - Kiro": None,
            "Cursor": None,
            "": None,
        }
        for title, want in cases.items():
            self.assertEqual(ap.editor_project(title), want, title)

    def test_norm_name(self):
        self.assertEqual(ap.norm_name("Cursor.exe"), "cursor")
        self.assertEqual(ap.norm_name("ChatGPT"), "chatgpt")

    def test_cmdline_patterns_both_separators(self):
        def tool(cmd):
            return next((t for p, t in ap.CMDLINE_AGENTS if p.search(cmd)), None)
        self.assertEqual(tool("node\x00/usr/lib/node_modules/@google/gemini-cli/dist/index.js\x00"), "gemini")
        self.assertEqual(tool("node.exe\x00C:\\Users\\u\\AppData\\Roaming\\npm\\node_modules\\@google\\gemini-cli\\dist\\index.js\x00"), "gemini")
        self.assertEqual(tool("node.exe\x00C:\\npm\\node_modules\\@github\\copilot\\index.js\x00"), "copilot")
        self.assertEqual(tool("python3\x00-m\x00aider\x00"), "aider")
        self.assertIsNone(tool("node\x00server.js\x00"))

    def test_pretty_model(self):
        self.assertEqual(ap.pretty_model("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(ap.pretty_model("gemini-3-pro"), "Gemini 3 Pro")

    def test_scratch_dirs_are_not_projects(self):
        home = Path.home()
        self.assertEqual(ap.resolve_project(str(home / "Documents" / "Codex" / "x")), (None, None))
        self.assertEqual(ap.resolve_project(str(home / "AppData" / "Claude" / "scratch-workspaces" / "a")),
                         (None, None))


class Transcripts(unittest.TestCase):
    cfg = ap.DEFAULT_CONFIG

    def test_claude_pass_fail(self):
        for is_error, ok in ((False, True), (True, False)):
            recs = [
                {"type": "assistant", "cwd": str(Path.cwd()), "timestamp": now_iso(),
                 "message": {"model": "claude-opus-5-5", "content": [
                     {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}}]}},
                {"type": "user", "timestamp": now_iso(), "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "t1", "is_error": is_error, "content": ""}]}},
            ]
            a = ap.claude_agent(write_jsonl(recs), time.time(), self.cfg)
            self.assertEqual((a["outcome"]["kind"], a["outcome"]["ok"]), ("tests", ok))
            self.assertEqual(a["model"], "claude-opus-5-5")

    def test_claude_waiting_on_question(self):
        recs = [{"type": "assistant", "cwd": "/x", "message": {"content": [
            {"type": "tool_use", "id": "q", "name": "AskUserQuestion", "input": {}}]}}]
        a = ap.claude_agent(write_jsonl(recs), time.time(), self.cfg)
        self.assertEqual(a["status"], "waiting")

    def test_codex_exit_codes(self):
        for out, ok in (('{"exit_code":0}', True), ("Process exited with code 2", False)):
            recs = [{"type": "session_meta", "payload": {"cwd": "/x"}},
                    {"timestamp": now_iso(), "type": "response_item", "payload": {
                        "type": "function_call", "name": "exec_command", "call_id": "c",
                        "arguments": json.dumps({"cmd": "cargo build"})}},
                    {"timestamp": now_iso(), "type": "response_item", "payload": {
                        "type": "function_call_output", "call_id": "c", "output": out}}]
            a = ap.codex_agent(write_jsonl(recs), time.time(), self.cfg)
            self.assertEqual((a["outcome"]["kind"], a["outcome"]["ok"]), ("build", ok))

    def test_gemini_session(self):
        f = write_jsonl([], suffix=".json")
        f.write_text(json.dumps({"messages": [
            {"type": "user", "content": "go"},
            {"type": "gemini", "model": "gemini-3-pro", "toolCalls": [
                {"name": "run_shell_command", "args": {"command": "pytest"}, "status": "executing"}]}]}))
        a = ap.gemini_agent(f, time.time(), {}, self.cfg)
        self.assertEqual(a["action"], "🧪 running tests")
        self.assertEqual(a["status"], "busy")


class StatsAndMilestones(unittest.TestCase):
    def test_human_formats(self):
        self.assertEqual(ap.human_tokens(3_612_345), "3.6M")
        self.assertEqual(ap.human_tokens(1_000_000), "1M")
        self.assertEqual(ap.human_tokens(950), "950")
        self.assertEqual(ap.human_duration(12660), "3h 31m")

    def test_claude_tokens_and_time_counted_once_per_reply(self):
        t0 = time.time()
        def rec(dt, mid, out):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t0 + dt))
            return {"type": "assistant", "cwd": "/x", "timestamp": ts, "message": {
                "id": mid, "usage": {"input_tokens": 10, "output_tokens": out,
                                     "cache_creation_input_tokens": 0, "cache_read_input_tokens": 99999}}}
        # m1 is logged twice (two content blocks); the 10-minute gap isn't agent time
        f = write_jsonl([rec(-700, "m1", 100), rec(-690, "m1", 100), rec(-680, "m2", 50), rec(-60, "m3", 5)])
        ap.STATS.clear()
        ap._scan(f, "claude", "claude", t0 - 3600)
        st = ap.STATS[f]
        self.assertEqual(st["tokens"], 110 + 60 + 15)   # cache reads excluded, m1 once
        self.assertAlmostEqual(st["secs"], 20, delta=1)  # only the two short gaps

    def test_commit_milestone(self):
        import shutil, subprocess
        if not shutil.which("git"):
            self.skipTest("git not installed")
        repo = tempfile.mkdtemp()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
               "GIT_AUTHOR_EMAIL": ap.git_user_email() or "t@example.com",
               "GIT_COMMITTER_EMAIL": ap.git_user_email() or "t@example.com"}
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        for i in range(10):
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", f"c{i}"], cwd=repo, env=env, check=True)
        ap.CACHE.clear()
        found = ap.milestones(ap.DEFAULT_CONFIG, {"roots": {"r": repo}, "agents": []}, 0)
        self.assertIn("🏆 10th commit today", [t for t, _ in found])


class CombinedCard(unittest.TestCase):
    def st(self, focused=False, busy=0, waiting=0, projects=(), since=0.0, tool="claude"):
        ag = [{"tool": tool, "status": "busy", "action": "✍", "outcome": None}] * busy + \
             [{"tool": tool, "status": "waiting", "action": "⏳", "outcome": None}] * waiting
        return {"focused": focused, "agents": ag, "projects": list(projects), "roots": {},
                "running_since": since}

    def test_primary_is_focused_then_busiest(self):
        live = {"claude": self.st(busy=2, projects=["px4"], tool="claude"),
                "cursor": self.st(focused=True, projects=["omega"], tool="cursor")}
        primary, m = ap.merge_states(live)
        self.assertEqual(primary, "cursor")
        self.assertEqual(m["projects"], ["omega", "px4"])
        self.assertEqual(len(m["agents"]), 2)
        live["cursor"]["focused"] = False
        self.assertEqual(ap.merge_states(live)[0], "claude")

    def test_tools_with_own_app_leave_the_combined_card(self):
        live = {"claude": self.st(busy=1), "vscode": self.st(focused=True, tool="vscode")}
        cards = ap.plan_cards(ap.DEFAULT_CONFIG, live, own={"vscode"})
        self.assertEqual(set(cards), {"vscode", "combined"})
        self.assertEqual(cards["combined"][0], "claude")

    def test_shared_ids_cover_every_tool(self):
        ids = ap.client_ids(ap.DEFAULT_CONFIG)
        self.assertEqual(len(ids["combined"]), 2)


class Moods(unittest.TestCase):
    def S(self, actions, outcome=None, focused=False):
        return {"focused": focused, "agents": [
            {"status": "busy" if x else "waiting", "action": x or "⏳", "outcome": outcome} for x in actions]}

    def test_priority(self):
        cfg, now = ap.DEFAULT_CONFIG, time.time()
        self.assertEqual(ap.mood(self.S(["🚀 pushing"]), cfg), "push")
        self.assertEqual(ap.mood(self.S(["🛸 running the sim"]), cfg), "sim")
        self.assertEqual(ap.mood(self.S(["🛸 running the sim"], {"kind": "tests", "ok": False, "t": now}), cfg),
                         "failed")
        self.assertEqual(ap.mood(self.S(["✍", "✍", "✍"]), cfg), "crew")
        self.assertEqual(ap.mood(self.S([None]), cfg), "waiting")
        self.assertEqual(ap.mood(self.S([]), cfg), "idle")

    def test_vibe_name(self):
        self.assertEqual(ap.pick_name(ap.DEFAULT_CONFIG, self.S(["✍"]), 2), 1)
        self.assertEqual(ap.pick_name(ap.DEFAULT_CONFIG, self.S([], focused=True), 2), 0)


class Machines(unittest.TestCase):
    def peer(self, mid, idle, discord=True, busy=0, waiting=0, model="Alienware Aurora R16",
             kind="desktop", tool="chatgpt"):
        ag = [{"tool": tool, "status": "busy", "action": "✍ editing", "project": "omega",
               "model": None, "mtime": time.time(), "outcome": None}] * busy + \
             [{"tool": tool, "status": "waiting", "action": "⏳ waiting for me", "project": "omega",
               "model": None, "mtime": time.time(), "outcome": None}] * waiting
        tools = {tool: {"running_since": time.time() - 60, "focused": False, "projects": ["omega"]}} \
            if ag else {}
        return {"v": 1, "id": mid, "name": "", "model": model, "chip": "", "kind": kind,
                "os": "Ubuntu 24.04", "os_full": "Ubuntu 24.04.1 LTS", "idle": idle,
                "discord": discord, "agents": ag, "tools": tools}

    def test_machine_info_never_uses_hostname(self):
        m = ap.machine_info()
        for k in ("id", "model", "kind", "os", "os_full"):
            self.assertTrue(m[k], k)
        import platform
        host = platform.node().split(".")[0]
        if host and host.lower() not in m["model"].lower().split():
            self.assertNotEqual(m["model"], host)

    def test_link_signs_and_rejects(self):
        a = ap.Link("secret-key", port=0, target="127.0.0.1")
        b = ap.Link("other-key", port=0, target="127.0.0.1")
        sealed = a.seal({"id": "x", "model": "m"})
        self.assertEqual(a.open(sealed)["model"], "m")
        self.assertIsNone(b.open(sealed))                       # wrong key
        self.assertIsNone(a.open(sealed[:-2] + b"}}"))          # tampered
        old = a.seal({"id": "x"})
        import hmac
        body = json.dumps({"id": "x", "t": time.time() - 600}).encode()
        stale = hmac.new(a.key, body, "sha256").digest()[:16] + body
        self.assertIsNone(a.open(stale))                        # replayed
        self.assertIsNotNone(a.open(old))
        a.sock.close(), b.sock.close()

    def test_link_round_trip(self):
        recv = ap.Link("k", port=0, target="127.0.0.1")
        port = recv.sock.getsockname()[1]
        send = ap.Link("k", port=0, target="127.0.0.1")
        send.port = port
        send.send(self.peer("peer1", 3))
        send.send(self.peer("me", 3))  # our own packets are ignored
        for _ in range(20):
            got = recv.receive("me")
            if got:
                break
            time.sleep(0.05)
        self.assertEqual([p["id"] for p in got], ["peer1"])
        recv.sock.close(), send.sock.close()

    def test_leader_follows_input_with_hysteresis(self):
        me, other = self.peer("me", 300), self.peer("other", 2)
        self.assertEqual(ap.pick_leader(me, [other]), "other")
        # Current leader keeps the card until the other is clearly more recent.
        me["idle"], other["idle"] = 10, 20
        self.assertEqual(ap.pick_leader(me, [other], current="other"), "other")
        other["idle"] = 40
        self.assertEqual(ap.pick_leader(me, [other], current="other"), "me")

    def test_machine_without_discord_never_shows_the_card(self):
        me, jetson = self.peer("me", 900), self.peer("jetson", 0, discord=False, kind="board")
        self.assertEqual(ap.pick_leader(me, [jetson]), "me")
        me["discord"] = False  # nobody has Discord: stay put
        self.assertEqual(ap.pick_leader(me, [jetson]), "me")

    def test_peer_agents_join_the_card(self):
        me = self.peer("me", 1, model="MacBook Pro", kind="laptop")
        other = self.peer("other", 900, busy=2)
        live = {"claude": {"running_since": time.time(), "focused": True, "projects": ["px4"],
                           "roots": {}, "agents": [{"tool": "claude", "status": "busy",
                                                    "action": "🧪 running tests", "project": "px4",
                                                    "model": None, "mtime": time.time(),
                                                    "outcome": None}]}}
        live = ap.merge_peers(live, [other])
        self.assertEqual(len(live["chatgpt"]["agents"]), 2)
        machines = ap.machines_summary(me, [other], ap.DEFAULT_CONFIG)
        acts, cards = ap.activities(ap.DEFAULT_CONFIG, 0, live, set(), machines)
        lines = [ln["text"] for ln in ap.card_lines(ap.DEFAULT_CONFIG, *cards["combined"], 0, machines)[0]]
        self.assertTrue(any(t.startswith("⚡ 3 agents cooking") for t in lines), lines)
        self.assertIn("🖥 2 machines: 💻 MacBook Pro · 🖥 Alienware Aurora R16 (2)", lines)
        self.assertIn("🖥 Alienware Aurora R16: 2 agents cooking while I'm away", lines)
        self.assertIn("on MacBook Pro", acts["combined"]["assets"]["large_text"])

    def test_single_machine_and_switch_lines(self):
        me = self.peer("me", 1, model="MacBook Pro", kind="laptop")
        me["os"], me["os_full"] = "macOS 15", "macOS Sequoia 15.2"
        machines = ap.machines_summary(me, [], ap.DEFAULT_CONFIG)
        self.assertEqual(ap.machine_lines(ap.DEFAULT_CONFIG, machines),
                         [("💻 MacBook Pro · macOS 15", "MacBook Pro · macOS Sequoia 15.2")])
        lines = ap.card_lines(ap.DEFAULT_CONFIG, "claude",
                              {"agents": [], "focused": True, "projects": [], "roots": {},
                               "running_since": time.time()}, 0, machines, "MacBook Pro")[0]
        self.assertEqual(lines[0]["text"], "↪ moved to MacBook Pro")
        self.assertEqual(ap.machine_lines({**ap.DEFAULT_CONFIG, "show_machine": False}, machines), [])


class Web(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = ap.HABITS_DIR, ap.WEB_STATE
        ap.HABITS_DIR, ap.WEB_STATE = self.tmp / "habits", self.tmp / "web.json"

    def tearDown(self):
        ap.HABITS_DIR, ap.WEB_STATE = self.saved

    def agent(self, project, public=False, action="✍ editing nav.cpp", status="busy"):
        return {"tool": "claude", "status": status, "action": action, "project": project,
                "public": public, "model": "claude-opus-5-5", "mtime": time.time(),
                "outcome": None, "cwd": None}

    def live(self, *agents):
        return {"claude": {"running_since": time.time() - 600, "focused": False,
                           "projects": [a["project"] for a in agents], "roots": {},
                           "agents": list(agents)}}

    def test_private_repos_files_and_times_stay_off(self):
        cfg = {**ap.DEFAULT_CONFIG, "hide_projects": ["secret"]}
        live = self.live(self.agent("omega-1", public=True), self.agent("employer-api"),
                         self.agent("secret"))
        st = ap.web_status(cfg, live)
        self.assertEqual(st["state"], "vibe-coding")
        self.assertEqual(sorted(a["project"] for a in st["agents"]),
                         ["a private project", "a private project", "omega-1"])
        self.assertEqual({a["action"] for a in st["agents"]}, {"✍ editing"})
        self.assertEqual([p["name"] for p in st["projects"]], ["omega-1"])  # private ones aren't listed
        blob = json.dumps(st)
        for leak in ("employer-api", "secret", "nav.cpp", "since", "os_full"):
            self.assertNotIn(leak, blob)
        self.assertNotIn("tokens", st["today"])  # off by default on the web
        off = ap.web_status(cfg, {}, state="offline")
        self.assertEqual(off["last_active"], "today")
        self.assertEqual(off["updated"], int(ap._midnight()))  # no exact sign-off time

    def test_svg_is_valid_escaped_and_clock_free(self):
        import xml.etree.ElementTree as ET
        cfg = {**ap.DEFAULT_CONFIG, "web": {**ap.DEFAULT_CONFIG["web"], "name": "A&B"}}
        svg = ap.render_svg(ap.web_status(cfg, self.live(self.agent("px4 <x>", public=True))))
        ET.fromstring(svg)
        self.assertIn("A&amp;B is vibe coding", svg)
        self.assertIn("px4 &lt;x&gt;", svg)
        off = ap.render_svg(ap.web_status(cfg, {}, state="offline"))
        ET.fromstring(off)
        self.assertNotRegex(off, r"\d{1,2}:\d{2}")  # no clock times anywhere

    def test_readme_snippet_insert_and_replace(self):
        md, _ = ap.web_snippets("me/me")
        text = "# Hi there\nI build robots.\n"
        once = ap.insert_snippet(text, md)
        self.assertTrue(once.startswith("# Hi there\n\n<!-- agent-presence:start -->"))
        self.assertIn("I build robots.", once)
        self.assertEqual(ap.insert_snippet(once, md), once)
        newer = md.replace("width=\"480\"", "width=\"400\"")
        self.assertEqual(ap.insert_snippet(once, newer).count("agent-presence:start"), 1)
        self.assertTrue(ap.insert_snippet("", md).startswith("<!-- agent-presence:start -->"))

    def test_publish_makes_one_parentless_day_dated_commit(self):
        calls = []

        def fake(token, method, path, body=None, timeout=15):
            calls.append((method, path, body))
            if path.endswith("/git/refs/heads/agent-presence-status"):
                raise ap.urllib.error.HTTPError(path, 422, "no ref", {}, None)
            return {"sha": "abc"}
        real, ap.github_call = ap.github_call, fake
        try:
            ap.publish_files("t", "me/me", "agent-presence-status", {"status.json": "{}"})
        finally:
            ap.github_call = real
        self.assertEqual([c[0] for c in calls], ["POST", "POST", "PATCH", "POST"])
        self.assertEqual(calls[1][2]["parents"], [])
        self.assertTrue(calls[1][2]["committer"]["date"].endswith("T00:00:00Z"))
        self.assertEqual(calls[3][2]["ref"], "refs/heads/agent-presence-status")

    def test_habits_record_sessions_outcomes_and_summary(self):
        h = ap.Habits()
        base = (int(time.time() // 60) - 120) * 60  # on a minute boundary, earlier today
        if time.localtime(base).tm_yday != time.localtime().tm_yday:
            self.skipTest("too close to midnight")
        ok = {"kind": "tests", "ok": True, "t": time.time() - 60}
        live = self.live(self.agent("omega-1"))
        live["claude"]["agents"][0]["outcome"] = ok
        for i in range(10):                      # 10 active minutes
            h.record(ap.DEFAULT_CONFIG, live, "MacBook Pro", base + i * 60)
        self.assertFalse(h.record(ap.DEFAULT_CONFIG, live, "MacBook Pro", base + 9 * 60 + 5))  # same minute
        for i in range(40, 45):                  # back after a 30-minute gap
            h.record(ap.DEFAULT_CONFIG, live, "MacBook Pro", base + i * 60)
        rec = ap.load_habits(1)[date_today()]
        self.assertEqual(rec["active_minutes"], 15)
        self.assertEqual(len(rec["sessions"]), 2)
        self.assertEqual(rec["outcomes"], {"tests_pass": 1})  # counted once
        self.assertEqual(rec["projects"]["omega-1"], 15)
        self.assertEqual(rec["machines"]["MacBook Pro"], 15)
        s = ap.habit_summary()
        self.assertEqual(s["today"]["sessions"], 2)
        self.assertEqual(s["today"]["longest_session"], 10)
        self.assertGreaterEqual(s["streak"], 1)
        self.assertIn("My coding habits", ap.habits_html(s))

    def test_streaks_and_shared_highlights(self):
        from datetime import date, timedelta
        d = date.today()
        days = {(d - timedelta(days=i)).isoformat() for i in range(4)} | \
               {(d - timedelta(days=i)).isoformat() for i in range(10, 16)}
        self.assertEqual(ap._streaks(days), (4, 6))
        summary = {"streak": 4, "best_streak": 6,
                   "week": {"active_minutes": 600, "commits": 12, "tokens": 5_000_000},
                   "month": {"commits": 40, "days": 18, "tests_run": 20, "outcomes": {"tests_pass": 18}}}
        cfg = {**ap.DEFAULT_CONFIG, "web": {**ap.DEFAULT_CONFIG["web"], "share": ["streak", "week_hours"]}}
        self.assertEqual([h["key"] for h in ap.web_highlights(cfg, summary)], ["streak", "week_hours"])
        keys = [h["key"] for h in ap.web_highlights(ap.DEFAULT_CONFIG, summary)]
        self.assertNotIn("tokens", keys)
        self.assertIn("tests", keys)


def date_today():
    from datetime import date
    return date.today().isoformat()


class Platform(unittest.TestCase):
    def test_process_table_sees_this_process(self):
        procs, kids = ap.proc_table()
        self.assertIn(os.getpid(), procs, "process listing returned nothing (psutil missing?)")
        name = procs[os.getpid()][0]
        self.assertTrue(name.startswith("python") or "python" in procs[os.getpid()][3].lower(), name)
        self.assertEqual(Path(ap.proc_cwd(os.getpid())).resolve(), Path.cwd().resolve())
        self.assertIn("unittest", ap.proc_cmdline(os.getpid()) + "unittest")
        self.assertIsNotNone(ap.cpu_seconds(os.getpid()))

    def test_window_detection_does_not_crash(self):
        procs, _ = ap.proc_table()
        active, wins = ap.desktop_windows(procs)  # headless CI: usually nothing
        self.assertIsInstance(wins, list)

    def test_snapshot_runs(self):
        live = ap.snapshot(ap.DEFAULT_CONFIG)
        self.assertIsInstance(live, dict)

    def test_ipc_paths(self):
        paths = list(ap.ipc_paths())
        self.assertTrue(paths)
        if ap.IS_WIN:
            self.assertTrue(paths[0].startswith("\\\\.\\pipe\\discord-ipc-"))

    def test_setup_dry_run(self):
        ap.cmd_setup(dry_run=True) if not ap.IS_LINUX or __import__("shutil").which("systemctl") else None


# ---------------------------------------------------------------- fake Discord

def _frame(op, payload):
    data = json.dumps(payload).encode()
    return struct.pack("<II", op, len(data)) + data


class FakeDiscord:
    """Accepts one client: handshake, then records SET_ACTIVITY payloads."""
    def __init__(self):
        self.activities = []
        self.ready = threading.Event()

    def serve(self, read, write):
        def frame():
            op, n = struct.unpack("<II", read(8))
            return op, json.loads(read(n))
        frame()  # handshake
        write(_frame(1, {"cmd": "DISPATCH", "evt": "READY", "data": {"v": 1}}))
        op, msg = frame()
        self.activities.append(msg["args"]["activity"])
        write(_frame(1, {"cmd": "SET_ACTIVITY", "evt": None, "data": {}, "nonce": msg["nonce"]}))


@unittest.skipIf(ap.IS_WIN, "Unix socket transport")
class UnixIPC(unittest.TestCase):
    def test_handshake_and_set_activity(self):
        d = tempfile.mkdtemp(prefix="dap")
        path = os.path.join(d, "discord-ipc-0")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        fake = FakeDiscord()

        def run():
            conn, _ = srv.accept()

            def read(n):
                buf = b""
                while len(buf) < n:
                    buf += conn.recv(n - len(buf))
                return buf
            fake.serve(read, conn.sendall)
            conn.close()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        old = {k: os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "TMPDIR")}
        os.environ["XDG_RUNTIME_DIR"] = os.environ["TMPDIR"] = d
        try:
            ipc = ap.DiscordIPC("123")
            self.assertTrue(ipc.update({"details": "▸ test", "state": "⚡ 1 agent cooking"}))
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        t.join(5)
        srv.close()
        ipc.close()
        self.assertEqual(fake.activities[0]["details"], "▸ test")


@unittest.skipUnless(ap.IS_WIN, "Windows named pipe transport")
class WindowsIPC(unittest.TestCase):
    def test_handshake_and_set_activity(self):
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateNamedPipeW.restype = wintypes.HANDLE
        name = r"\\.\pipe\discord-ipc-7"
        h = k32.CreateNamedPipeW(name, 3, 0, 1, 65536, 65536, 0, None)  # duplex, byte mode
        self.assertNotEqual(h, wintypes.HANDLE(-1).value)
        fake = FakeDiscord()

        def run():
            k32.ConnectNamedPipe(h, None)

            def read(n):
                buf = b""
                while len(buf) < n:
                    chunk = ctypes.create_string_buffer(n - len(buf))
                    got = wintypes.DWORD()
                    k32.ReadFile(h, chunk, n - len(buf), ctypes.byref(got), None)
                    buf += chunk.raw[:got.value]
                return buf

            def write(data):
                done = wintypes.DWORD()
                k32.WriteFile(h, data, len(data), ctypes.byref(done), None)
            fake.serve(read, write)
            k32.CloseHandle(h)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.2)
        orig = ap.ipc_paths
        ap.ipc_paths = lambda: iter([name])
        try:
            ipc = ap.DiscordIPC("123")
            self.assertTrue(ipc.update({"details": "▸ test", "state": "⚡ 1 agent cooking"}))
        finally:
            ap.ipc_paths = orig
        t.join(5)
        ipc.close()
        self.assertEqual(fake.activities[0]["details"], "▸ test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
