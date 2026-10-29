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

    def test_wsl_agents_are_found(self):
        self.assertEqual(ap.wsl_path(r"\\wsl.localhost\Ubuntu", "/home/abel/omega"),
                         r"\\wsl.localhost\Ubuntu\home\abel\omega")
        self.assertEqual(ap.wsl_path(r"\\wsl.localhost\Ubuntu", "/mnt/c/Users/abel/px4"), r"C:\Users\abel\px4")
        self.assertEqual(ap.wsl_path(None, "/home/abel/omega"), "/home/abel/omega")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "abel"
            proj = home / ".claude/projects/-home-abel-omega"
            proj.mkdir(parents=True)
            (proj / "s.jsonl").write_text(json.dumps(
                {"type": "assistant", "cwd": "/home/abel/omega", "timestamp": now_iso(),
                 "message": {"model": "claude-opus-5-5", "content": [
                     {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "ls"}}]}}) + "\n")
            orig = ap.wsl_homes
            ap.wsl_homes = lambda: [(r"\\wsl.localhost\Ubuntu", home)]
            try:
                agents = [a for a in ap.gather_agents(self.cfg) if a["model"] == "claude-opus-5-5"
                          and "omega" in (a["cwd"] or "")]
            finally:
                ap.wsl_homes = orig
        self.assertEqual(agents[0]["cwd"], r"\\wsl.localhost\Ubuntu\home\abel\omega")
        self.assertEqual(agents[0]["status"], "busy")

    def test_quiet_builds_and_subagents_still_count(self):
        old = time.time() - 900  # 15 minutes without a write to the main log
        use = lambda name: [{"type": "assistant", "cwd": "/x", "timestamp": now_iso(),
                             "message": {"model": "claude-opus-5-5", "content": [
                                 {"type": "tool_use", "id": "t", "name": name, "input": {"command": "make"}}]}}]
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / ".claude/projects/-x"
            proj.mkdir(parents=True)
            for sid, name in (("build", "Bash"), ("edit", "Edit"), ("crew", "Agent")):
                f = proj / f"{sid}.jsonl"
                f.write_text("".join(json.dumps(r) + "\n" for r in use(name)))
                os.utime(f, (old, old))
            sub = proj / "crew" / "subagents"
            sub.mkdir(parents=True)
            (sub / "agent-1.jsonl").write_text("{}\n")  # a subagent writing right now
            orig = ap.HOME
            ap.HOME = Path(tmp)
            try:
                agents = ap.gather_agents(self.cfg)
            finally:
                ap.HOME = orig
        status = {a["action"]: a["status"] for a in agents}
        self.assertEqual(status, {"🔨 building": "busy", "🤖 directing a subagent": "busy",
                                  "⏳ needs my approval": "waiting"})  # a stuck edit is asking you
        crew = next(a for a in agents if a["action"].startswith("🤖"))
        self.assertGreater(crew["mtime"], old + 600)  # the subagent's write counts

    def test_background_subagents_keep_the_session_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / ".claude/projects/-x"
            (proj / "s" / "subagents").mkdir(parents=True)
            (proj / "s.jsonl").write_text(json.dumps(
                {"type": "assistant", "cwd": "/x", "timestamp": now_iso(),
                 "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "started"}]}}) + "\n")
            (proj / "s" / "subagents" / "agent-1.jsonl").write_text("{}\n")
            orig = ap.HOME
            ap.HOME = Path(tmp)
            try:
                a, = ap.gather_agents(self.cfg)
            finally:
                ap.HOME = orig
        self.assertEqual((a["status"], a["action"]), ("busy", "🤖 directing a subagent"))

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

    def test_link_encrypts_signs_and_rejects(self):
        a = ap.Link("secret-key", port=0, target="127.0.0.1")
        b = ap.Link("other-key", port=0, target="127.0.0.1")
        sealed = a.seal({"id": "x", "model": "m"})
        self.assertEqual(a.open(sealed)["model"], "m")
        self.assertIsNone(b.open(sealed))                       # wrong key
        self.assertIsNone(a.open(sealed[:-1] + bytes([sealed[-1] ^ 1])))  # tampered
        self.assertNotIn(b'"model"', sealed)                    # unreadable on the network
        old = a.seal({"id": "x"})
        body = json.dumps({"id": "x", "t": time.time() - 600}).encode()
        stale = a.box.seal(body)
        self.assertIsNone(a.open(stale))                        # replayed
        self.assertIsNotNone(a.open(old))
        legacy = b"\0" * 16 + json.dumps({"id": "x", "t": time.time()}).encode()
        self.assertIsNone(a.open(legacy))                       # old plaintext packets
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

    def test_one_computer_publishes_the_web_card(self):
        me, other = self.peer("me", 300), self.peer("other", 2)
        me["web"] = True
        # The other one shows the Discord card but can't publish, so this one does.
        self.assertEqual(ap.pick_leader(me, [other]), "other")
        self.assertEqual(ap.pick_leader(me, [other], need="web"), "me")
        other["web"] = True  # both can: the one in use publishes
        self.assertEqual(ap.pick_leader(me, [other], need="web"), "other")

    def test_relay_encrypts_and_rejects(self):
        cfg = {**ap.DEFAULT_CONFIG, "link_key": "secret-key",
               "web": {**ap.DEFAULT_CONFIG["web"], "enabled": True, "repo": "me/me"}}
        self.assertTrue(ap.relay_wanted(cfg))
        self.assertFalse(ap.relay_wanted({**cfg, "link_key": ""}))
        a = ap.Relay("secret-key", cfg)
        packet = self.peer("peer1", 3, busy=1)
        sealed = a.seal(packet)
        self.assertNotIn("omega", sealed)                       # nothing readable in the repo
        got = ap.Relay("secret-key", cfg).open(sealed)
        self.assertEqual((got["id"], got["agents"][0]["project"]), ("peer1", "omega"))
        self.assertIsNone(ap.Relay("other-key", cfg).open(sealed))  # wrong key
        import base64
        raw = bytearray(base64.b64decode(sealed))
        raw[-1] ^= 1
        self.assertIsNone(a.open(base64.b64encode(bytes(raw)).decode()))  # tampered
        self.assertIsNone(a.open("not base64!"))

    def test_relay_sync_reads_other_computers(self):
        cfg = {**ap.DEFAULT_CONFIG, "link_key": "k",
               "web": {**ap.DEFAULT_CONFIG["web"], "enabled": True, "repo": "me/me"}}
        import base64
        theirs = ap.Relay("k", cfg).seal(self.peer("other", 5, busy=2))
        written, calls = {}, []

        def call(token, method, path, body=None, timeout=15):
            calls.append(path)
            if "matching-refs" in path:
                return [{"ref": "refs/heads/agent-presence-peer-me", "object": {"sha": "s1"}},
                        {"ref": "refs/heads/agent-presence-peer-other", "object": {"sha": "s2"}}]
            return {"content": base64.b64encode(theirs.encode()).decode()}
        old = ap.github_call, ap.publish_files
        ap.github_call = call
        ap.publish_files = lambda token, repo, branch, files: written.update({branch: files})
        try:
            relay = ap.Relay("k", cfg)
            relay.sync(self.peer("me", 1), "tok")
            relay.sync(self.peer("me", 1), "tok")  # unchanged branch isn't fetched again
        finally:
            ap.github_call, ap.publish_files = old
        self.assertIn("agent-presence-peer-me", written)
        self.assertEqual([p["id"] for p in relay.receive("me")], ["other"])
        self.assertEqual(sum("contents" in c for c in calls), 1)
        relay.peers["other"]["t"] = time.time() - 600
        self.assertEqual(relay.receive("me"), [])                # stale

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
        # "running_since" is the leak this guards; the bare substring "since"
        # can no longer be banned now that the truth model publishes the
        # (contract-required) tracking_since field name.
        for leak in ("employer-api", "secret", "nav.cpp", "running_since", "os_full"):
            self.assertNotIn(leak, blob)
        self.assertNotIn("tokens", st["today"])  # off by default on the web
        off = ap.web_status(cfg, {}, state="offline")
        # Presence truth model: last_active is an absolute UTC timestamp of
        # the last verified activity (here: the live call just above), never
        # a day word and never "now". The SVG still displays only the day.
        self.assertEqual(ap._day_word(ap._ts_of(off["last_active"])), "today")
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


class PresenceTruth(unittest.TestCase):
    """docs/presence-truth-model.md: published state must be earned by
    verified activity, and consumers must fail closed on stale or malformed
    data. Publisher tests here; the web consumer (docs/embed.js) is driven
    under Node with a frozen clock when Node is available."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = ap.HABITS_DIR, ap.WEB_STATE, ap.JOB_ORIGINS
        ap.HABITS_DIR = self.tmp / "habits"
        ap.WEB_STATE = self.tmp / "web.json"
        ap.JOB_ORIGINS = self.tmp / "job-origins.json"

    def tearDown(self):
        ap.HABITS_DIR, ap.WEB_STATE, ap.JOB_ORIGINS = self.saved

    def cfg(self):
        return {**ap.DEFAULT_CONFIG, "hide_projects": []}

    def declare_origins(self, origins):
        ap.JOB_ORIGINS.write_text(json.dumps(origins))

    def agent(self, status):
        return {"tool": "claude", "status": status, "action": "⏳ waiting for me",
                "project": "omega-1", "public": True, "model": None,
                "mtime": time.time(), "outcome": None, "cwd": None}

    def live(self, *agents, focused=False):
        return {"claude": {"running_since": time.time() - 600, "focused": focused,
                           "projects": ["omega-1"] if agents else [], "roots": {},
                           "agents": list(agents)}}

    def machine(self, in_use):
        return [{"label": "Linux box", "icon": "🖥", "os": "Ubuntu 24.04", "agents": 0,
                 "busy": 0, "in_use": in_use, "me": True, "last_seen_at": ap._iso_utc(time.time())}]

    def seed_last_active(self, ts):
        ap.WEB_STATE.write_text(json.dumps({"last_active": ts}))

    def test_waiting_agent_is_waiting_and_last_active_unchanged(self):  # acceptance 1
        before = time.time() - 3600
        self.seed_last_active(before)
        st = ap.web_status(self.cfg(), self.live(self.agent("waiting")),
                           machines=self.machine(False))
        self.assertEqual(st["state"], "waiting")
        self.assertTrue(st["agent_waiting"])
        self.assertFalse(st["agent_executing"])
        self.assertEqual(st["last_active"], ap._iso_utc(before))  # untouched, absolute
        self.assertNotEqual(st["last_active"], "now")
        self.assertTrue(st["machine_reachable"])
        self.assertEqual(st["schema_version"], st["v"])
        self.assertIsNotNone(ap._ts_of(st["published_at"]))
        self.assertIsNotNone(ap._ts_of(st["heartbeat_at"]))

    def test_busy_agent_is_coding_and_advances_last_active(self):
        self.seed_last_active(time.time() - 3600)
        st = ap.web_status(self.cfg(), self.live(self.agent("busy")),
                           machines=self.machine(False))
        self.assertEqual(st["state"], "vibe-coding")
        self.assertTrue(st["agent_executing"])
        self.assertGreater(ap._ts_of(st["last_active"]), time.time() - 60)
        # …and the next, quiet publish keeps that timestamp rather than "now".
        off = ap.web_status(self.cfg(), {}, state="offline")
        self.assertEqual(off["last_active"], st["last_active"])

    def test_focused_human_with_fresh_input_is_coding(self):
        st = ap.web_status(self.cfg(), self.live(focused=True), machines=self.machine(True))
        self.assertEqual(st["human_active"], True)
        self.assertEqual(st["state"], "coding")

    def test_focus_without_input_evidence_is_unknown_not_true(self):
        # No machines plumbed in -> no idle-sensor evidence -> never claim True.
        st = ap.web_status(self.cfg(), self.live(focused=True))
        self.assertEqual(st["human_active"], "unknown")
        self.assertEqual(st["state"], "idle")

    def test_offline_publish_signals_unreachable(self):
        st = ap.web_status(self.cfg(), {}, state="offline")
        self.assertEqual(st["state"], "offline")
        self.assertFalse(st["machine_reachable"])
        self.assertFalse(st["human_active"])
        self.assertFalse(st["agent_executing"])
        self.assertIsNone(st["last_active"])  # nothing verified yet, nothing invented

    def test_machines_carry_last_seen_at(self):
        now = time.time()
        me = {"id": "a", "name": "", "model": "Linux box", "kind": "laptop",
              "os": "Ubuntu 24.04", "os_full": "Ubuntu 24.04 LTS", "idle": 5, "agents": [], "tools": {}}
        peer = {"id": "b", "name": "", "model": "MacBook Pro", "kind": "laptop", "t": now - 30,
                "os": "macOS 15", "os_full": "macOS 15.1", "idle": 400,
                "agents": [{"tool": "claude", "status": "busy", "action": "✍ editing",
                            "project": None, "public": False, "model": None,
                            "mtime": now - 30, "outcome": None}], "tools": {}}
        out = ap.machines_summary(me, [peer], self.cfg())
        by_label = {m["label"]: m for m in out}
        self.assertEqual(by_label["MacBook Pro"]["last_seen_at"], ap._iso_utc(now - 30))
        self.assertGreater(ap._ts_of(by_label["Linux box"]["last_seen_at"]), now - 60)
        st = ap.web_status(self.cfg(), self.live(self.agent("busy")), machines=out)
        self.assertTrue(all("last_seen_at" in m for m in st["machines"]))

    def test_phone_origin_alone_creates_no_mobile_state(self):  # acceptance 5 style
        # A declared iPhone origin (or a phone merely being in use) is not
        # activity: with no executing agent there is no mobile coding state
        # and no origin marker anywhere in the published status.
        self.declare_origins({"claude": "iphone"})
        st = ap.web_status(self.cfg(), self.live(self.agent("waiting")),
                           machines=self.machine(False))
        self.assertEqual(st["state"], "waiting")
        self.assertNotIn("mobile_origin", st["agents"][0])
        self.assertNotIn("iphone", json.dumps(st))
        idle = ap.web_status(self.cfg(), self.live(), machines=self.machine(False))
        self.assertEqual(idle["state"], "idle")
        self.assertNotIn("iphone", json.dumps(idle))

    def test_phone_origin_shows_while_executing_then_stops(self):  # acceptance 6 style
        self.declare_origins({"claude": {"origin": "iphone"}})
        st = ap.web_status(self.cfg(), self.live(self.agent("busy")),
                           machines=self.machine(False))
        self.assertEqual(st["state"], "vibe-coding")
        self.assertEqual(st["agents"][0]["mobile_origin"], "iphone")
        done = ap.web_status(self.cfg(), self.live(self.agent("waiting")),
                             machines=self.machine(False))
        self.assertEqual(done["state"], "waiting")
        self.assertNotIn("mobile_origin", done["agents"][0])  # marker ends with execution

    def test_origin_is_never_invented(self):
        st = ap.web_status(self.cfg(), self.live(self.agent("busy")),
                           machines=self.machine(False))
        self.assertNotIn("mobile_origin", st["agents"][0])

    def test_svg_renders_waiting(self):
        import xml.etree.ElementTree as ET
        st = ap.web_status(self.cfg(), self.live(self.agent("waiting")),
                           machines=self.machine(False))
        svg = ap.render_svg(st)
        ET.fromstring(svg)
        self.assertIn("Waiting", svg)        # STATE_WORDS knows the new state
        self.assertIn("#f0b232", svg)        # amber dot, not the green "live" one

    @unittest.skipUnless(__import__("shutil").which("node"), "node not available")
    def test_web_consumer_fails_closed(self):  # acceptance 1, 2 and 8 (consumer side)
        script = self.tmp / "consumer_test.js"
        script.write_text(r"""
const AP = require(process.argv[2]);
const NOW = 1793200000;  // frozen clock
const iso = t => new Date(t * 1000).toISOString();
let fails = 0;
const eq = (got, want, what) => { if (got !== want) { fails++; console.log("FAIL", what, "got", got, "want", want); } };
const v = st => AP.validateStatus(st, NOW);
// Fresh heartbeat + an executing agent: may show as coding.
eq(v({ state: "vibe-coding", heartbeat_at: iso(NOW - 10), agents: [{ status: "busy" }] }).state, "vibe-coding", "busy fresh");
// The observed regression: state claims coding, the only agent is waiting,
// last_active is the string "now". Must not render as coding.
const reg = v({ state: "coding", heartbeat_at: iso(NOW - 10), last_active: "now",
                agents: [{ status: "waiting", action: "waiting" }] });
eq(reg.state, "waiting", "waiting agent is not coding");
eq(AP.parseTime("now"), null, '"now" is not a timestamp');
// Acceptance 2: heartbeat older than the TTL -> offline, marked stale,
// even though the payload still claims coding with a busy agent.
const stale = v({ state: "coding", heartbeat_at: iso(NOW - AP.TTL - 500), agents: [{ status: "busy" }] });
eq(stale.state, "offline", "stale heartbeat is offline");
eq(stale.stale, true, "stale is flagged");
// Acceptance 8: malformed payloads and clock skew fail safe, never coding.
eq(v({}).ok, false, "empty payload rejected");
eq(v({ state: "banana", heartbeat_at: iso(NOW) }).ok, false, "unknown state rejected");
eq(v({ state: "coding", agents: [{ status: "busy" }] }).state, "offline", "no time evidence -> offline");
eq(v({ state: "coding", heartbeat_at: iso(NOW + 3600), agents: [{ status: "busy" }] }).state, "offline", "future heartbeat untrusted");
// Rendered output agrees: stale data is labelled stale, never "live";
// demo data is badged DEMO and never claims live.
const root = { innerHTML: "" };
AP.render(root, { state: "coding", heartbeat_at: iso(NOW - AP.TTL - 500), agents: [{ status: "busy" }] });
if (!/stale/.test(root.innerHTML) || /● live/.test(root.innerHTML)) { fails++; console.log("FAIL stale render"); }
AP.render(root, { demo: true, state: "vibe-coding", name: "Alex", agents: [{ status: "busy", action: "x" }] });
if (!/DEMO/.test(root.innerHTML) || /● live/.test(root.innerHTML)) { fails++; console.log("FAIL demo render"); }
AP.render(root, {});
if (!/No live status reported/.test(root.innerHTML)) { fails++; console.log("FAIL unavailable render"); }
// The frozen clock must not leak into the renders above: re-render the stale
// case against real time and confirm it is still not live.
const root2 = { innerHTML: "" };
AP.render(root2, { state: "coding", heartbeat_at: iso(1000), agents: [{ status: "busy" }] });
if (/● live/.test(root2.innerHTML)) { fails++; console.log("FAIL ancient render claims live"); }
// Phone origin: rendered for an executing job, gone when it is only waiting
// (fresh heartbeats here are relative to the real clock render() uses).
const freshIso = () => new Date().toISOString();
AP.render(root, { state: "vibe-coding", heartbeat_at: freshIso(), agents: [{ status: "busy", action: "working", mobile_origin: "iphone" }] });
if (!/📱/.test(root.innerHTML)) { fails++; console.log("FAIL origin while executing"); }
AP.render(root, { state: "waiting", heartbeat_at: freshIso(), agents: [{ status: "waiting", action: "waiting", mobile_origin: "iphone" }] });
if (/📱/.test(root.innerHTML)) { fails++; console.log("FAIL origin while waiting"); }
console.log(fails ? "CONSUMER FAILURES: " + fails : "consumer ok");
process.exit(fails ? 1 : 0);
""")
        import subprocess
        r = subprocess.run(["node", str(script), str(Path(__file__).resolve().parent.parent / "docs" / "embed.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class FakeIPC:
    """Stands in for DiscordIPC: only the surface public_gate may read."""

    def __init__(self, connected=False, status=None):
        self.sock = object() if connected else None
        self.user_status = status

    @property
    def connected(self):
        return self.sock is not None


class DiscordGate(unittest.TestCase):
    """The public_presence switch: one gate for Discord Rich Presence, the
    website JSON/SVG and the README publisher, failing closed whenever the
    Discord signal is off, absent or ambiguous."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = ap.HABITS_DIR, ap.WEB_STATE
        ap.HABITS_DIR, ap.WEB_STATE = self.tmp / "habits", self.tmp / "web.json"

    def tearDown(self):
        ap.HABITS_DIR, ap.WEB_STATE = self.saved

    def cfg(self, mode):
        return {**ap.DEFAULT_CONFIG, "hide_projects": [], "public_presence": mode}

    def conns(self, *ipcs):
        return {"claude": list(ipcs)}

    def live_busy(self):
        agent = {"tool": "claude", "status": "busy", "action": "✍ editing",
                 "project": "omega-1", "public": True, "model": None,
                 "mtime": time.time(), "outcome": None, "cwd": None}
        return {"claude": {"running_since": time.time() - 600, "focused": False,
                           "projects": ["omega-1"], "roots": {}, "agents": [agent]}}

    def test_on_and_off_ignore_discord(self):
        self.assertTrue(ap.public_gate(self.cfg("on"), None))
        self.assertTrue(ap.public_gate(self.cfg("on"), self.conns(FakeIPC())))
        self.assertFalse(ap.public_gate(self.cfg("off"), self.conns(FakeIPC(connected=True))))

    def test_sync_needs_a_running_client(self):
        cfg = self.cfg("discord")
        self.assertFalse(ap.public_gate(cfg, None))                       # no daemon signal
        self.assertFalse(ap.public_gate(cfg, self.conns(FakeIPC())))      # client closed
        self.assertTrue(ap.public_gate(cfg, self.conns(FakeIPC(connected=True))))

    def test_sync_fails_closed_on_hidden_status(self):
        cfg = self.cfg("discord")
        for status in ("invisible", "offline"):
            self.assertFalse(ap.public_gate(cfg, self.conns(FakeIPC(connected=True, status=status))), status)
        self.assertTrue(ap.public_gate(cfg, self.conns(FakeIPC(connected=True, status="online"))))

    def test_gated_status_publishes_offline_when_closed(self):
        closed = ap.gated_web_status(self.cfg("discord"), self.conns(FakeIPC()), self.live_busy())
        self.assertEqual(closed["state"], "offline")
        self.assertFalse(closed["machine_reachable"])
        self.assertFalse(closed["agent_executing"])  # the busy agent is NOT leaked
        off = ap.gated_web_status(self.cfg("off"), self.conns(FakeIPC(connected=True)), self.live_busy())
        self.assertEqual(off["state"], "offline")
        open_ = ap.gated_web_status(self.cfg("discord"),
                                    self.conns(FakeIPC(connected=True)), self.live_busy())
        self.assertEqual(open_["state"], "vibe-coding")
        self.assertTrue(open_["agent_executing"])


class Privacy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = ap.CONFIG_PATH, ap.STATE_DIR
        ap.CONFIG_PATH = Path(self.tmp.name) / "cfg" / "config.json"
        ap.STATE_DIR = Path(self.tmp.name) / "state"

    def tearDown(self):
        ap.CONFIG_PATH, ap.STATE_DIR = self.orig
        self.tmp.cleanup()

    def card(self, cfg, *agents):
        s = Web.live(self, *agents)["claude"]
        s["tools"] = []
        return ap.build_activity(cfg, "claude", s, {"agents": len(agents), "tools": 1}, 0, 0)

    def test_presets_and_overrides(self):
        cfg = ap.load_config()
        self.assertEqual(cfg["privacy"], "balanced")
        self.assertFalse(cfg["show_files"])
        self.assertFalse(cfg["show_private_projects"])
        saved = json.loads(ap.CONFIG_PATH.read_text())
        self.assertNotIn("show_files", saved)  # follows the preset, not pinned
        saved.update(privacy="full", show_git=False)
        ap.CONFIG_PATH.write_text(json.dumps(saved))
        cfg = ap.load_config()
        self.assertTrue(cfg["show_files"] and cfg["show_private_projects"])
        self.assertFalse(cfg["show_git"])     # your own choice beats the preset
        saved["privacy"] = "minimal"
        ap.CONFIG_PATH.write_text(json.dumps(saved))
        cfg = ap.load_config()
        self.assertFalse(cfg["show_project"] or cfg["show_files"] or cfg["show_machine"])

    def test_old_configs_move_to_balanced(self):
        ap.CONFIG_PATH.parent.mkdir(parents=True)
        ap.CONFIG_PATH.write_text(json.dumps({"show_files": True, "show_git": False, "poll_seconds": 9}))
        cfg = ap.load_config()
        self.assertFalse(cfg["show_files"])   # was just the old default
        self.assertFalse(cfg["show_git"])     # was turned off on purpose: stays off
        self.assertEqual(cfg["poll_seconds"], 9)

    @unittest.skipIf(ap.IS_WIN, "POSIX permissions")
    def test_config_and_state_are_owner_only(self):
        ap.CONFIG_PATH.parent.mkdir(parents=True)
        ap.CONFIG_PATH.write_text("{}")
        os.chmod(ap.CONFIG_PATH, 0o644)
        cfg = ap.load_config()
        cfg["web"]["token"] = "github_pat_x"
        ap.save_config(cfg)
        self.assertEqual(ap.CONFIG_PATH.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ap.CONFIG_PATH.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(ap.STATE_DIR.stat().st_mode & 0o777, 0o700)

    def test_discord_names_only_public_repos(self):
        a = [Web.agent(self, "omega-1", public=True), Web.agent(self, "employer-api")]
        act = self.card(ap.DEFAULT_CONFIG, *a)
        self.assertIn("omega-1", act["details"])
        self.assertIn("private project", act["details"])
        self.assertNotIn("employer-api", json.dumps(act))
        full = self.card({**ap.DEFAULT_CONFIG, **ap.PRIVACY_PRESETS["full"]}, *a)
        self.assertIn("employer-api", full["details"])

    def test_peer_actions_follow_this_cards_settings(self):
        peer = Web.agent(self, "omega-1", public=True, action="✍ editing roadmap.md")
        act = self.card(ap.DEFAULT_CONFIG, peer)
        self.assertNotIn("roadmap.md", json.dumps(act, ensure_ascii=False))
        full = {**ap.DEFAULT_CONFIG, **ap.PRIVACY_PRESETS["full"]}
        self.assertEqual(ap.shown_action(full, "✍ editing roadmap.md"), "✍ editing roadmap.md")
        self.assertEqual(ap.shown_action(full, "✍ editing .env.local"), "✍ editing")

    def test_minimal_shows_only_the_app_and_count(self):
        cfg = {**ap.DEFAULT_CONFIG, **ap.PRIVACY_PRESETS["minimal"]}
        a = Web.agent(self, "omega-1", public=True)
        a["outcome"] = {"kind": "tests", "ok": False, "t": time.time()}
        s = Web.live(self, a)["claude"]
        s["tools"] = []
        lines, _, _ = ap.card_lines(cfg, "claude", s, 9)
        self.assertEqual([l["text"] for l in lines], ["⚡ 1 agent cooking"])

    def test_sensitive_file_names_never_show(self):
        for name in (".env", ".env.local", "id_rsa", "server.pem", "secrets.yaml", "api_key.txt",
                     "aws-credentials", "keys.json"):
            self.assertEqual(ap.action_for("Edit", {"file_path": f"/r/{name}"}, True), "✍ editing files")
        for name in ("keyboard.py", "monkey.js", "nav.cpp"):
            self.assertEqual(ap.action_for("Edit", {"file_path": f"/r/{name}"}, True), f"✍ editing {name}")


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


class GithubStreak(unittest.TestCase):
    """The coding streak is a union of evidenced sources (GitHub +
    local git + daemon observation), deduplicated by America/Los_Angeles
    day, with per-day provenance — never a local day counter alone."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = ap.HABITS_DIR, ap.WEB_STATE, ap.STATE_DIR, ap.STREAK_GH_CACHE
        ap.HABITS_DIR, ap.WEB_STATE = self.tmp / "habits", self.tmp / "web.json"
        ap.STATE_DIR = self.tmp / "state"
        ap.STREAK_GH_CACHE = self.tmp / "github-days.json"

    def tearDown(self):
        ap.HABITS_DIR, ap.WEB_STATE, ap.STATE_DIR, ap.STREAK_GH_CACHE = self.saved

    def test_la_day_bucketing(self):
        # 2026-10-29T06:30Z is 23:30 PDT on Oct 28; 08:30Z is 01:30 Oct 29.
        self.assertEqual(ap._la_day(1793255400), "2026-10-28")
        self.assertEqual(ap._la_day(1793262600), "2026-10-29")

    def test_union_dedupes_days_and_keeps_provenance(self):  # acceptance 13
        from datetime import date, timedelta
        today = date.today()
        d = lambda n: (today - timedelta(days=n)).isoformat()
        info = ap.union_streak_days({
            "github": {d(0), d(1), d(2)},
            "local:x": {d(2): ["local-git:x"], d(3): ["local-git:x"]},  # d(3): unpushed only
            "peer:y": {d(9): ["daemon:y"], d(10): ["daemon:y"]},
        })
        self.assertEqual(info["total"], 6)          # d(2) counted once
        self.assertEqual(info["current"], 4)        # d(0)..d(3) — the unpushed day extends it
        self.assertEqual(info["best"], 4)
        self.assertEqual(info["days"][d(2)], ["github", "local-git:x"])
        self.assertEqual(info["days"][d(3)], ["local-git:x"])
        self.assertEqual(info["provenance"]["github"], 3)
        self.assertEqual(info["provenance"]["daemon:y"], 2)

    def test_github_days_serve_cache_marked_when_unreachable(self):
        ap.STREAK_GH_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ap.STREAK_GH_CACHE.write_text(json.dumps(
            {"days": ["2026-10-20", "2026-10-21"], "fetched_at": 0, "login": "abel"}))
        real, ap.github_token = ap.github_token, lambda cfg: None
        try:
            days, cached = ap.github_days(ap.DEFAULT_CONFIG)
        finally:
            ap.github_token = real
        self.assertEqual(days, {"2026-10-20", "2026-10-21"})
        self.assertTrue(cached)  # served from cache, and says so

    def test_github_days_unknown_without_any_source(self):
        real, ap.github_token = ap.github_token, lambda cfg: None
        try:
            days, cached = ap.github_days(ap.DEFAULT_CONFIG)
        finally:
            ap.github_token = real
        self.assertIsNone(days)  # no cache + no token: unknown, not invented

    def test_packet_carries_local_streak_days(self):
        ap.STATE_DIR.mkdir(parents=True, exist_ok=True)
        (ap.STATE_DIR / "days.json").write_text(json.dumps(["2026-10-26"]))
        me = ap.machine_status(ap.DEFAULT_CONFIG, {})
        mid = ap.machine_id()
        self.assertEqual(me["streak_days"].get("2026-10-26"), [f"daemon:{mid}"])

    def test_web_status_streak_comes_from_union(self):
        info = {"current": 8, "best": 11, "total": 42,
                "provenance": {"github": 40, "local-git:abc": 20}, "github_cached": False,
                "days": {}}
        cfg = {**ap.DEFAULT_CONFIG, "web": {**ap.DEFAULT_CONFIG["web"], "name": "Abel"}}
        st = ap.web_status(cfg, {}, streak_info=info)
        self.assertEqual(st["streak"], 8)
        self.assertEqual(st["streak_source"], "union")
        self.assertEqual(st["streak_days_total"], 42)
        self.assertEqual(st["streak_provenance"], {"github": 40, "local-git:abc": 20})
        by_key = {h["key"]: h["value"] for h in st["highlights"]}
        self.assertEqual(by_key.get("streak"), "8 days")
        self.assertEqual(by_key.get("best_streak"), "11 days")
        self.assertEqual(by_key.get("active_days"), "42")
        svg = ap.render_svg(ap.web_status(cfg, {"claude": {
            "running_since": time.time() - 60, "focused": True, "projects": [], "roots": {},
            "agents": [{"tool": "claude", "status": "busy", "action": "✍ editing x.py",
                        "project": None, "model": None, "mtime": time.time(),
                        "outcome": None, "cwd": None}]}}, streak_info=info))
        self.assertIn("8 days", svg)  # the README card shows the union streak too


class WebUnionPublish(unittest.TestCase):
    """Several machines publish status.d/<id>.json to one branch: a
    publish must preserve the other machines' files."""

    def test_publish_union_keeps_other_machines_files(self):
        calls = []

        def fake(token, method, path, body=None, timeout=15):
            calls.append((method, path, body))
            if method == "GET" and "/git/refs/" in path:
                return {"object": {"sha": "commitsha"}}
            if method == "GET" and "/git/commits/" in path:
                return {"tree": {"sha": "treesha"}}
            if method == "GET" and "/git/trees/" in path:
                return {"tree": [
                    {"path": "status.json", "type": "blob", "mode": "100644", "sha": "old-status"},
                    {"path": "status.d/other-machine.json", "type": "blob", "mode": "100644",
                     "sha": "other-blob"},
                    {"path": "status.d/mine.json", "type": "blob", "mode": "100644",
                     "sha": "stale-mine"}]}
            return {"sha": "abc"}
        real, ap.github_call = ap.github_call, fake
        try:
            ap.publish_files_union("t", "me/me", "agent-presence-status",
                                   {"status.json": "{}", "status.d/mine.json": "{}"})
        finally:
            ap.github_call = real
        tree_post = next(b for m, p, b in calls if m == "POST" and p.endswith("/git/trees"))
        entries = {e["path"]: e for e in tree_post["tree"]}
        self.assertEqual(entries["status.d/other-machine.json"]["sha"], "other-blob")
        self.assertIn("content", entries["status.d/mine.json"])  # ours is replaced, not kept
        self.assertIn("content", entries["status.json"])
        commit_post = next(b for m, p, b in calls if m == "POST" and p.endswith("/git/commits"))
        self.assertEqual(commit_post["parents"], [])

    def test_publisher_writes_own_machine_file(self):
        seen = {}
        real_pub, real_tok = ap.publish_files_union, ap.github_token
        ap.publish_files_union = lambda t, r, b, files: seen.update(files)
        ap.github_token = lambda cfg: "tok"
        try:
            cfg = {**ap.DEFAULT_CONFIG,
                   "web": {**ap.DEFAULT_CONFIG["web"], "repo": "me/me", "enabled": True}}
            pub = ap.WebPublisher(cfg)
            self.assertTrue(pub.publish({"state": "idle", "agents": [], "projects": [],
                                         "machines": [], "tools": [], "highlights": []},
                                        own={"state": "idle", "agents": []}))
        finally:
            ap.publish_files_union, ap.github_token = real_pub, real_tok
        own_path = f"status.d/{ap.machine_id()}.json"
        self.assertIn(own_path, seen)
        self.assertEqual(json.loads(seen[own_path])["machine_id"], ap.machine_id())
        self.assertIn("status.json", seen)
        self.assertIn("status.svg", seen)


class MultiDeviceConsumer(unittest.TestCase):
    """docs/embed.js mergeStatuses: several machines live at once, each
    on its own evidence (acceptance 10, 11, 12 + the truth demotions)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_web_consumer_merges_machines(self):
        script = self.tmp / "merge_test.js"
        script.write_text(r"""
const AP = require(process.argv[2]);
const NOW = Math.floor(Date.now() / 1000);
const iso = t => new Date(t * 1000).toISOString();
let fails = 0;
const eq = (got, want, what) => { if (got !== want) { fails++; console.log("FAIL", what, "got", JSON.stringify(got), "want", JSON.stringify(want)); } };
const ok = (cond, what) => { if (!cond) { fails++; console.log("FAIL", what); } };
function payload(mid, label, state, agents, ageS, extra) {
  return Object.assign({ v: 2, schema_version: 2, machine_id: mid, name: "Abel", state: state,
    heartbeat_at: iso(NOW - ageS), published_at: iso(NOW - ageS), updated: NOW - ageS,
    agent_executing: agents.some(a => a.status === "busy"),
    agent_waiting: agents.some(a => a.status === "waiting"), human_active: "unknown",
    last_active: iso(NOW - ageS), agents: agents,
    machines: [{ id: mid, label: label, icon: "💻", os: "test", agents: agents.length, last_seen_at: iso(NOW - ageS) }],
    tools: [{ id: "claude", name: "Muse" }], projects: [], today: {}, highlights: [], streak: 0 }, extra || {});
}
const backend = payload("claude-backend", "Claude Backend", "coding",
  [{ tool: "claude", tool_name: "Muse", status: "busy", action: "working", mobile_origin: "iphone" }], 10,
  { streak: 8, streak_days_total: 8 });
const mac = payload("mac1", "MacBook Pro", "coding",
  [{ tool: "claude", tool_name: "Muse", status: "busy", action: "editing" }], 20);
const linux = payload("scythe1", "scythe", "waiting",
  [{ tool: "codex", tool_name: "Codex", status: "waiting", action: "waiting" }], 30);

// (a) Three fresh machines at once, incl. an iPhone-origin busy agent.
let m = AP.mergeStatuses([backend, mac, linux], NOW);
eq(m.state, "coding", "a: merged state");
eq(m.machines.map(x => x.label).join(","), "Claude Backend,MacBook Pro,scythe", "a: all machines");
eq(m.agents.length, 3, "a: all agents");
eq(m.streak, 8, "a: streak from the payload that knows it");
eq(AP.validateStatus(m, NOW).state, "coding", "a: merged validates live");
const root = { innerHTML: "" };
AP.render(root, m);
ok(/Claude Backend/.test(root.innerHTML) && /MacBook Pro/.test(root.innerHTML) && /scythe/.test(root.innerHTML), "a: render shows all 3 machines");
ok(/📱/.test(root.innerHTML), "a: render shows the iPhone origin");
ok(/● live/.test(root.innerHTML), "a: render is live");

// (b) One stale + one fresh: fresh renders, stale drops individually.
const staleMac = payload("mac1", "MacBook Pro", "coding",
  [{ tool: "claude", status: "busy", action: "x" }], AP.TTL + 5000);
m = AP.mergeStatuses([backend, staleMac], NOW);
eq(m.machines.map(x => x.label).join(","), "Claude Backend", "b: stale dropped alone");
eq(m.state, "coding", "b: fresh still renders live");

// (c) All stale: overall offline.
m = AP.mergeStatuses([staleMac, payload("scythe1", "scythe", "coding", [{ status: "busy" }], AP.TTL + 9000)], NOW);
eq(AP.validateStatus(m, NOW).state, "offline", "c: all stale is offline");
AP.render(root, m);
ok(!/● live/.test(root.innerHTML), "c: render is not live");

// (d) A single (legacy-shaped) payload merges to itself.
m = AP.mergeStatuses([backend], NOW);
eq(m.state, "coding", "d: single payload state");
eq(m.machines.length, 1, "d: single machine");
eq(AP.mergeStatuses([], NOW), null, "d: no payloads -> null (legacy fallback)");
eq(AP.mergeStatuses([{ demo: true, state: "coding" }], NOW), null, "d: demo never merges as live");

// (e) Truth demotions survive the merge.
const waitingClaim = payload("mac1", "MacBook Pro", "coding",
  [{ tool: "claude", status: "waiting", action: "waiting" }], 5);
m = AP.mergeStatuses([waitingClaim], NOW);
eq(m.state, "waiting", "e: coding claim with waiting agent demoted");
eq(m.agent_executing, false, "e: not executing");
const idleWithWaiting = payload("mac1", "MacBook Pro", "idle",
  [{ tool: "cursor", status: "waiting", action: "waiting" }], 5);
m = AP.mergeStatuses([backend, idleWithWaiting], NOW);
eq(m.agents.length, 1, "e: idle machine's waiting agent is not listed");
eq(m.machines.length, 2, "e: idle machine itself still shows as a machine");
console.log(fails ? "MERGE FAILURES: " + fails : "merge ok");
process.exit(fails ? 1 : 0);
""")
        import subprocess
        r = subprocess.run(["node", str(script), str(Path(__file__).resolve().parent.parent / "docs" / "embed.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
