import base64
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_chat import WebChat


class WebChatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.chat = WebChat(self.root)
        self.port = self.chat.start(port=0)
        self.addCleanup(self.chat.close)
        self.processes = []
        self.addCleanup(self.stop_agents)

    def stop_agents(self):
        self.chat.detach()
        for process in self.processes:
            process.terminate()
            process.wait(timeout=3)
            process.stdin.close()
            process.stdout.close()

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        defaults = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        defaults.update(headers or {})
        body = json.dumps(payload) if payload is not None else None
        connection.request(method, path, body=body, headers=defaults)
        response = connection.getresponse()
        content = response.read()
        status = response.status
        connection.close()
        return status, content

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail("Agent event did not arrive")

    def attach_agent(self):
        source = '''
import json, sys, time
def emit(**event):
    print("VEYRA_EVENT " + json.dumps(event), flush=True)
print("runtime diagnostic", flush=True)
emit(type="ready")
for line in sys.stdin:
    message = json.loads(line)
    time.sleep(.15)
    emit(type="message", text="Reply: " + message["text"])
    emit(type="done")
'''
        process = subprocess.Popen([sys.executable, "-u", "-c", source], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.processes.append(process)
        self.chat.attach(process)
        self.wait_for(lambda: self.chat.ready)
        return process

    def test_page_available_before_agent_and_messages_round_trip(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Message Veyra", html)
        self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"})[0], 503)
        self.attach_agent()
        self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"})[0], 202)
        self.assertEqual(self.request("POST", "/api/messages", {"text": "duplicate"})[0], 409)
        self.wait_for(lambda: not self.chat.busy)
        status, data = self.request("GET", "/api/events?after=0")
        events = json.loads(data)["events"]
        self.assertEqual(status, 200)
        self.assertEqual([e["text"] for e in events], ["runtime diagnostic", "hello", "Reply: hello"])
        self.assertEqual(events[1]["role"], "user")

    def test_foreign_origin_and_rebinding_host_cannot_submit(self):
        self.attach_agent()
        self.assertEqual(self.request("POST", "/api/messages", {"text": "/approve"},
                         {"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"},
                         {"Origin": ""})[0], 403)
        self.assertFalse(self.chat.busy)

    def test_stale_approval_cannot_run_after_restart(self):
        self.attach_agent()
        with self.chat.lock:
            self.chat.agent_event({"type": "approval", "approval_id": "old", "text": "$ whoami"})
        self.assertEqual(self.request("POST", "/api/messages", {"text": "/approve", "approval_id": "wrong"})[0], 409)
        self.chat.detach()
        self.attach_agent()
        self.assertEqual(self.request("POST", "/api/messages", {"text": "/approve", "approval_id": "old"})[0], 409)
        self.assertIsNone(self.chat.approval_id)
        self.assertTrue(any("cancelled" in e["text"] for e in self.chat.snapshot(0)["events"]))

    def test_transcript_survives_restart_and_cursor_avoids_duplicates(self):
        self.chat.publish("First message", role="assistant")
        first = self.chat.snapshot(0)
        restarted = WebChat(self.root)
        self.assertEqual(restarted.snapshot(0)["events"], first["events"])
        restarted.publish("Second message")
        later = restarted.snapshot(first["cursor"])
        self.assertEqual([e["text"] for e in later["events"]], ["Second message"])
        self.assertFalse(later["status"]["ready"])

    def test_invalid_messages_and_paths_are_rejected(self):
        for payload in ([], {"text": ""}, {"text": 7}, {"text": "a" * 20001}):
            self.assertEqual(self.request("POST", "/api/messages", payload)[0], 400)
        self.assertEqual(self.request("GET", "/api/events?after=oops")[0], 400)
        self.assertEqual(self.request("GET", "/../state/systemd.env")[0], 404)

    def test_network_access_requires_password_for_page_transcript_and_commands(self):
        self.attach_agent()
        handler = self.chat.server.RequestHandlerClass
        credential = "Basic " + base64.b64encode(("veyra:" + self.chat.network_password).encode()).decode()
        with patch.object(handler, "local_client", return_value=False):
            for path in ("/", "/chat.js", "/api/events"):
                self.assertEqual(self.request("GET", path)[0], 401)
                self.assertEqual(self.request("GET", path, headers={"Authorization": "Basic wrong"})[0], 401)
                self.assertEqual(self.request("GET", path, headers={"Authorization": credential})[0], 200)
            self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"})[0], 401)
            self.assertFalse(self.chat.busy)
            self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"},
                             {"Authorization": credential, "Origin": "https://attacker.example"})[0], 403)
            self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"},
                             {"Authorization": credential})[0], 202)

    def test_default_password_and_reminder_survive_restart(self):
        restarted = WebChat(self.root)
        self.assertEqual(self.chat.network_password, "veyra")
        self.assertEqual(restarted.network_password, "veyra")
        self.assertTrue(restarted.snapshot(0)["status"]["password_is_default"])
        self.assertNotIn(self.chat.network_password, json.dumps(self.chat.snapshot(0)))
        self.assertEqual(self.chat.database.stat().st_mode & 0o777, 0o600)

    def test_password_update_requires_authentication_origin_and_current_password(self):
        handler = self.chat.server.RequestHandlerClass
        old = "Basic " + base64.b64encode(b"veyra:veyra").decode()
        new_password = "changed-password-\u2713"
        payload = {"current_password": "veyra", "new_password": new_password}
        with patch.object(handler, "local_client", return_value=False):
            self.assertEqual(self.request("POST", "/api/password", payload)[0], 401)
            self.assertEqual(self.request("POST", "/api/password", payload,
                             {"Authorization": old, "Origin": "https://attacker.example"})[0], 403)
            self.assertEqual(self.request("POST", "/api/password", {**payload, "current_password": "wrong"},
                             {"Authorization": old})[0], 403)
            self.assertEqual(self.chat.network_password, "veyra")
            self.assertEqual(self.request("POST", "/api/password", payload, {"Authorization": old})[0], 200)
            self.assertEqual(self.request("GET", "/api/events", headers={"Authorization": old})[0], 401)
            new = "Basic " + base64.b64encode(("veyra:" + new_password).encode()).decode()
            status, data = self.request("GET", "/api/events", headers={"Authorization": new})
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(data)["status"]["password_is_default"])
            self.assertEqual(json.loads(data)["events"], [])
            self.assertNotIn(new_password, data.decode())
        restarted = WebChat(self.root)
        self.assertEqual(restarted.network_password, new_password)
        self.assertFalse(restarted.snapshot(0)["status"]["password_is_default"])

    def test_local_password_update_also_checks_current_password_and_validates_input(self):
        for payload in ([], {}, {"current_password": None, "new_password": "valid-password"},
                        {"current_password": "veyra", "new_password": "short"},
                        {"current_password": "veyra", "new_password": "a" * 257}):
            self.assertEqual(self.request("POST", "/api/password", payload)[0], 400)
        self.assertEqual(self.request("POST", "/api/password", {
            "current_password": "wrong", "new_password": "valid-password",
        })[0], 403)
        self.assertEqual(self.chat.network_password, "veyra")


if __name__ == "__main__":
    unittest.main()
