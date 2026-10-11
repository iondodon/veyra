import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


class QuietWebLifecycleTests(unittest.TestCase):
    def test_terminal_is_only_url_and_chat_survives_rejected_and_accepted_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(Path(__file__).resolve().parents[1], root / "supervisor",
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
            (root / "agent").mkdir()
            (root / "agent/START").write_text(
                "#!/bin/sh\nexec " + sys.executable + " \"$(dirname \"$0\")/fixture.py\" \"$@\"\n"
            )
            (root / "agent/START").chmod(0o755)
            fixture = '''
import json, sys
VERSION = {version!r}
if "--self-test" in sys.argv:
    print("self-test diagnostic " + VERSION)
    raise SystemExit({exit_code})
def emit(**event):
    print("VEYRA_EVENT " + json.dumps(event), flush=True)
print("child diagnostic " + VERSION, flush=True)
emit(type="ready")
for line in sys.stdin:
    message = json.loads(line)
    emit(type="message", text=VERSION + ": " + message["text"])
    emit(type="done")
'''
            def commit(version, exit_code=0):
                (root / "agent/fixture.py").write_text(fixture.format(version=version, exit_code=exit_code))
                subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
                subprocess.run(["git", "-C", str(root), "commit", "-qm", version], check=True, capture_output=True)
            subprocess.run(["git", "init", "-qb", "main", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Veyra Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@veyra.local"], check=True)
            (root / ".gitignore").write_text("/state/\n**/__pycache__/\n")
            commit("first")
            process = subprocess.Popen(
                [sys.executable, str(root / "supervisor/supervisor")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={"PATH": os.environ.get("PATH", "")},
            )
            def request(method="GET", text=None):
                connection = http.client.HTTPConnection("127.0.0.1", 3000, timeout=1)
                connection.request(method, "/api/messages" if text else "/api/events",
                    body=json.dumps({"text": text}) if text else None,
                    headers={"Origin": "http://127.0.0.1:3000", "Content-Type": "application/json"})
                response = connection.getresponse()
                result = json.loads(response.read())
                connection.close()
                self.assertIn(response.status, (200, 202))
                return result
            def wait_for(predicate):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        data = request()
                        if predicate(data):
                            return data
                    except (OSError, http.client.HTTPException):
                        pass
                    time.sleep(.03)
                self.fail("Veyra web lifecycle did not reach expected state")
            try:
                wait_for(lambda d: d["status"]["ready"])
                request("POST", "hello")
                wait_for(lambda d: any(e["text"] == "first: hello" for e in d["events"]))
                commit("broken", exit_code=7)
                data = wait_for(lambda d: any("self-test failed" in e["text"] for e in d["events"]))
                self.assertTrue(data["status"]["ready"])
                request("POST", "still here")
                wait_for(lambda d: any(e["text"] == "first: still here" for e in d["events"]))
                commit("second")
                previous_session = data["status"]["session_id"]
                wait_for(lambda d: d["status"]["ready"] and d["status"]["session_id"] != previous_session)
                request("POST", "updated")
                wait_for(lambda d: any(e["text"] == "second: updated" for e in d["events"]))
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(stdout, "Open Veyra at http://localhost:3000\n")
            self.assertEqual(stderr, "")
            self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
