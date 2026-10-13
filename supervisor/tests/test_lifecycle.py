"""Exercise lifecycle with an opaque shell executable, no agent imports."""
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

class BlackBoxLifecycleTests(unittest.TestCase):
    def test_opaque_agent_restart_and_independent_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root / "supervisor", ignore=shutil.ignore_patterns("tests", "__pycache__"))
            (root / "agent").mkdir()
            start = root / "agent/START"
            start.write_text('#!/bin/sh\n[ "$#" = 0 ] || exit 99\necho opaque-process\nexec sleep 120\n')
            start.chmod(0o755)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-qb", "main"); git("config", "user.name", "Test"); git("config", "user.email", "test@example.test")
            (root / ".gitignore").write_text('/state/\n**/__pycache__/\n')
            git("add", "."); git("commit", "-qm", "first")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
            proc = subprocess.Popen([sys.executable, str(root / "supervisor/supervisor")], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, "VEYRA_RECOVERY_PORT": str(port)})
            def request(payload=None):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                c.request("POST" if payload else "GET", "/api/recovery/action" if payload else "/api/recovery", body=json.dumps(payload) if payload else None,
                    headers={"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"})
                res=c.getresponse(); data=json.loads(res.read()); c.close(); return data
            def wait(predicate):
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    try:
                        state=request()
                        if predicate(state): return state
                    except OSError: pass
                    time.sleep(.05)
                self.fail("Lifecycle timeout")
            try:
                first=git("rev-parse", "HEAD")
                wait(lambda s:s["ready"] and s["last_working"]==first)
                start.write_text('#!/bin/sh\nexit 7\n')
                git("add", "agent");git("commit", "-qm", "broken")
                wait(lambda s:bool(s["error"]))
                start.write_text('uncommitted draft')
                request({"action":"retry"})
                wait(lambda s:not s["ready"] and not s["busy"] and "uncommitted" in (s["error"] or ""))
                self.assertTrue((root / "state/evolution/supervisor.log").exists())
            finally:
                proc.terminate(); out,err=proc.communicate(timeout=8)
            self.assertEqual(proc.returncode,0)
            self.assertEqual(err,b'')
            self.assertIn(b'Open local recovery',out)
