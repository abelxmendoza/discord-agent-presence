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
