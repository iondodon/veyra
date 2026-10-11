import base64
import http.client
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "supervisor"))
from web_gateway import WebGateway
sys.path.insert(0, str(ROOT / "agent"))
from web_chat import WebChat


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.gateway = WebGateway(self.root)
        self.port = self.gateway.start(port=0)
        self.addCleanup(self.gateway.close)
        self.chat = WebChat(self.root)
        self.chat.start(socket_path=self.root / "agent.sock", token="private-fixture-token")
        self.addCleanup(self.chat.close)
        self.gateway.endpoint = (self.root / "agent.sock", "private-fixture-token")
        self.gateway.active_revision = "fixture"
        self.chat.agent_event({"type": "ready"})

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        defaults = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        defaults.update(headers or {})
        connection.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=defaults)
        response = connection.getresponse()
        result = (response.status, response.read())
        connection.close()
        return result

    def test_chat_and_recovery_share_listener_and_fallback_survives_agent(self):
        self.assertIn(b"Message Veyra", self.request("GET", "/")[1])
        self.assertIn(b"Veyra recovery", self.request("GET", "/recovery")[1])
        self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"})[0], 202)
        self.assertEqual(self.chat.inbox.get_nowait()["text"], "hello")
        self.gateway.detach()
        for path in ("/", "/recovery", "/recovery.css", "/recovery.js", "/api/recovery"):
            self.assertEqual(self.request("GET", path)[0], 200)
        self.assertIn(b"Veyra recovery", self.request("GET", "/")[1])
        self.assertEqual(self.request("GET", "/api/events")[0], 503)

    def test_lan_authentication_and_local_key_restriction_survive_proxy(self):
        handler = self.gateway.server.RequestHandlerClass
        credential = "Basic " + base64.b64encode(b"veyra:veyra").decode()
        with patch.object(handler, "local_client", return_value=False):
            for path in ("/", "/recovery", "/api/recovery", "/api/events"):
                self.assertEqual(self.request("GET", path)[0], 401)
                self.assertEqual(self.request("GET", path, headers={"Authorization": credential})[0], 200)
            status, body = self.request("GET", "/api/settings", headers={"Authorization": credential, "X-Veyra-Local": "1"})
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)["can_save_keys"])
            status, body = self.request("GET", "/api/events", headers={"Authorization": credential, "X-Veyra-Local": "1"})
            self.assertFalse(json.loads(body)["status"]["local_client"])
            self.assertEqual(self.request("POST", "/api/keys", {"provider": "openai", "key": "sk-fixture-key"},
                             {"Authorization": credential, "X-Veyra-Local": "1"})[0], 403)
            self.assertFalse(self.chat.credentials.path.exists())

    def test_changed_password_is_used_when_agent_is_offline(self):
        handler = self.gateway.server.RequestHandlerClass
        old = "Basic " + base64.b64encode(b"veyra:veyra").decode()
        new = "Basic " + base64.b64encode(b"veyra:fixture-new-password").decode()
        with patch.object(handler, "local_client", return_value=False):
            self.assertEqual(self.request("POST", "/api/password", {
                "current_password": "veyra", "new_password": "fixture-new-password"}, {"Authorization": old})[0], 200)
            self.gateway.detach()
            self.assertEqual(self.request("GET", "/recovery", headers={"Authorization": old})[0], 401)
            self.assertEqual(self.request("GET", "/recovery", headers={"Authorization": new})[0], 200)

    def test_recovery_rejects_foreign_origin_host_and_unconfirmed_actions(self):
        self.assertEqual(self.request("GET", "/recovery", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/recovery/action", {"action": "retry"},
                         {"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/recovery/action", {"action": "discard", "confirmed": True})[0], 409)
        self.assertIsNone(self.gateway.recovery.pending)

    def test_private_socket_rejects_missing_or_wrong_token(self):
        self.gateway.endpoint = (self.root / "agent.sock", "wrong-token")
        self.assertFalse(self.gateway.ready())
        self.assertEqual(self.request("GET", "/api/events")[0], 403)
