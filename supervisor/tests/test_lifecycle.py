import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest


class QuietWebLifecycleTests(unittest.TestCase):
    def test_shared_port_survives_failed_versions_and_runs_confirmed_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = Path(__file__).resolve().parents[2]
            shutil.copytree(project / "supervisor", root / "supervisor",
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
            (root / "agent").mkdir()
            for name in ("web_chat.py", "release_notes.py", "chat.html", "chat.css", "chat.js"):
                shutil.copy2(project / "agent" / name, root / "agent" / name)
            (root / "agent/START").write_text(
                "#!/bin/sh\nexec " + sys.executable + " \"$(dirname \"$0\")/fixture.py\" \"$@\"\n")
            (root / "agent/START").chmod(0o755)
            fixture = '''
import os, sys
from pathlib import Path
from web_chat import WebChat
VERSION = {version!r}
if "--self-test" in sys.argv:
    print("self-test diagnostic " + VERSION)
    raise SystemExit({exit_code})
chat = WebChat(Path(__file__).resolve().parents[1])
chat.start(socket_path=os.environ["VEYRA_AGENT_SOCKET"], token=os.environ["VEYRA_AGENT_TOKEN"])
print("child diagnostic " + VERSION, flush=True)
chat.agent_event({{"type": "ready"}})
while True:
    message = chat.inbox.get()
    with chat.lock:
        chat.agent_event({{"type": "message", "text": VERSION + ": " + message["text"]}})
        chat.agent_event({{"type": "done"}})
'''
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args], check=True,
                                      capture_output=True).stdout.decode().strip()
            def commit(version, exit_code=0):
                (root / "agent/fixture.py").write_text(fixture.format(version=version, exit_code=exit_code))
                git("add", ".")
                git("commit", "-qm", version)
                return git("rev-parse", "HEAD")
            git("init", "-qb", "main")
            git("config", "user.name", "Veyra Test")
            git("config", "user.email", "test@veyra.local")
            (root / ".gitignore").write_text("/state/\n/workspace/\n**/__pycache__/\n")
            first = commit("first")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            process = subprocess.Popen([sys.executable, str(root / "supervisor/supervisor")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={"PATH": os.environ.get("PATH", ""), "VEYRA_WEB_PORT": str(port)})

            def request(method="GET", path="/api/recovery", payload=None):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                connection.request(method, path, body=json.dumps(payload) if payload is not None else None,
                    headers={"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"})
                response = connection.getresponse()
                body = response.read()
                connection.close()
                return response.status, json.loads(body) if body.startswith(b"{") else body

            def wait_for(predicate, path="/api/recovery"):
                deadline = time.monotonic() + 10
                last = None
                while time.monotonic() < deadline:
                    try:
                        status, last = request(path=path)
                        if status == 200 and predicate(last):
                            return last
                    except (OSError, http.client.HTTPException):
                        pass
                    time.sleep(.03)
                self.fail("Veyra lifecycle did not reach expected state: " + str(last))

            def chat(text, expected):
                self.assertEqual(request("POST", "/api/messages", {"text": text})[0], 202)
                wait_for(lambda data: any(event["text"] == expected for event in data["events"]), "/api/events")

            def recover(action):
                status, preview = request("POST", "/api/recovery/preview", {"action": action})
                self.assertEqual(status, 200, preview)
                status, result = request("POST", "/api/recovery/action", {
                    "action": action, "token": preview["token"], "confirmed": True})
                self.assertEqual(status, 202, result)

            try:
                wait_for(lambda data: data["ready"] and data["last_working"] == first)
                chat("hello", "first: hello")
                broken = commit("broken", exit_code=7)
                data = wait_for(lambda data: data["error"] and any("self-test failed" in line for line in data["diagnostics"]))
                self.assertTrue(data["ready"])
                chat("still here", "first: still here")
                recover("restore")
                wait_for(lambda data: data["ready"] and data["revision"] != broken and not data["busy"] and not data["error"])
                self.assertEqual(git("rev-parse", "HEAD^"), broken)
                self.assertEqual(git("branch", "--show-current"), "main")
                chat("restored", "first: restored")
                second = commit("second")
                wait_for(lambda data: data["ready"] and data["active_revision"] == second and data["last_working"] == second)
                chat("updated", "second: updated")
                (root / "agent/fixture.py").write_text("broken uncommitted source\n")
                (root / "agent/new.py").write_text("untracked draft\n")
                (root / "state/keep.txt").write_text("persistent data\n")
                self.assertEqual(request("POST", "/api/recovery/action", {"action": "retry"})[0], 202)
                wait_for(lambda data: not data["ready"] and data["error"] and not data["busy"])
                status, page = request(path="/")
                self.assertEqual(status, 200)
                self.assertIn(b"Veyra recovery", page)
                recover("discard")
                wait_for(lambda data: data["ready"] and not data["error"] and not data["busy"])
                self.assertFalse((root / "agent/new.py").exists())
                self.assertEqual((root / "state/keep.txt").read_text(), "persistent data\n")
                chat("recovered", "second: recovered")
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(stdout, f"Open Veyra at http://localhost:{port}\n")
            self.assertEqual(stderr, "")
            self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
