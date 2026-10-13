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

    def test_application_routes_are_not_a_supervisor_allowlist(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method), patch.object(self.gateway, "forward", return_value=(201, b"agent response", "text/plain")) as forward:
                status, body = self.request(method, "/new-feature?mode=1", {"value": "new"}, {"Content-Type": "text/plain"})
                self.assertEqual((status, body), (201, b"agent response"))
                self.assertEqual(forward.call_args.args[:2], (method, "/new-feature?mode=1"))
                forward.reset_mock()
                self.assertEqual(self.request(method, "/new-feature", {}, {"Origin": "http://evil.test"})[0], 403)
                forward.assert_not_called()
        with patch.object(self.gateway, "forward", return_value=(404, b"agent 404", "text/plain")):
            self.assertEqual(self.request("POST", "/unknown", {}) , (404, b"agent 404"))
        self.gateway.detach()
        self.assertEqual(self.request("POST", "/new-feature", {})[0], 503)

    def test_recovery_namespace_is_not_forwarded(self):
        with patch.object(self.gateway, "forward") as forward:
            self.assertEqual(self.request("POST", "/api/recovery/unknown", {})[0], 404)
            self.assertEqual(self.request("PUT", "/api/recovery/action", {})[0], 404)
            with patch.object(self.gateway.recovery, "preview", return_value={"ok": True}):
                self.assertEqual(self.request("POST", "/api/recovery/preview?test=1", {"action": "retry"})[0], 200)
            self.assertEqual(self.request("GET", "/api/recovery/unknown")[0], 404)
            forward.assert_not_called()

    def test_transport_limit_is_not_application_route_specific(self):
        with patch.object(self.gateway, "forward", return_value=(200, b"ok", "text/plain")) as forward:
            self.assertEqual(self.request("POST", "/future-upload", {"data": "x" * 100001})[0], 200)
            forward.assert_called_once()
            forward.reset_mock()
            self.assertEqual(self.request("POST", "/future-upload", {}, {"Content-Length": str(29 * 1024 * 1024 + 1)})[0], 413)
            self.assertEqual(self.request("POST", "/future-upload", {}, {"Transfer-Encoding": "chunked"})[0], 400)
            self.assertEqual(self.request("POST", "/api/recovery/action", {}, {"Content-Length": "100001"})[0], 413)
            forward.assert_not_called()

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

    def test_public_ipv4_supports_forwarded_ports_and_requires_password(self):
        credential = "Basic " + base64.b64encode(b"veyra:veyra").decode()
        for host in (f"8.8.8.8:{self.port}", "8.8.8.8:43123", "8.8.8.8"):
            with self.subTest(host=host):
                for path in ("/", "/chat.css", "/api/events", "/recovery", "/api/recovery"):
                    self.assertEqual(self.request("GET", path, headers={"Host": host})[0], 401)
                    self.assertEqual(self.request("GET", path, headers={
                        "Host": host, "Authorization": credential})[0], 200)
                self.assertEqual(self.request("GET", "/", headers={
                    "Host": host, "Authorization": "Basic wrong-password"})[0], 401)

    def test_public_access_preserves_remote_context_and_origin_checks(self):
        host = f"8.8.8.8:{self.port}"
        headers = {"Host": host, "Origin": f"http://{host}",
                   "Authorization": "Basic " + base64.b64encode(b"veyra:veyra").decode(),
                   "X-Veyra-Local": "1", "X-Forwarded-For": "127.0.0.1",
                   "X-Forwarded-Host": f"localhost:{self.port}"}
        status, body = self.request("GET", "/api/events", headers=headers)
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["status"]["local_client"])
        status, body = self.request("GET", "/api/settings", headers=headers)
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["can_save_keys"])
        self.assertEqual(self.request("POST", "/api/keys", {
            "provider": "openai", "key": "sk-fixture-key"}, headers)[0], 403)
        self.assertFalse(self.chat.credentials.path.exists())
        for origin in ("", "null", "https://attacker.example", f"http://127.0.0.1:{self.port}"):
            self.assertEqual(self.request("POST", "/api/messages", {"text": "blocked"},
                             {**headers, "Origin": origin})[0], 403)
        self.assertEqual(self.request("POST", "/api/messages", {"text": "blocked"},
                         {**headers, "Authorization": ""})[0], 401)
        self.assertEqual(self.request("POST", "/api/messages", {"text": "public hello"}, headers)[0], 202)
        self.assertEqual(self.chat.inbox.get_nowait()["text"], "public hello")
        self.gateway.detach()
        self.assertEqual(self.request("GET", "/recovery", headers=headers)[0], 200)
        self.assertEqual(self.request("GET", "/recovery", headers={"Host": host})[0], 401)

    def test_public_access_rejects_invalid_hosts_and_duplicate_host_headers(self):
        credential = "Basic " + base64.b64encode(b"veyra:veyra").decode()
        for host in ("attacker.example", "8.8.8.8.attacker.example:3000", "user@8.8.8.8:3000",
                     "8.8.8.8:0", "8.8.8.8:65536", "8.8.8.8:" + "9" * 5000, "8.8.8.8:", "8.8.8.8:not-a-port",
                     "8.8.8.8:3000/path", "8.8.8.8?query", "8.8.8.8#fragment", "008.8.8.8:3000",
                     "192.168.100.1:3000", "0.0.0.0:3000", "224.0.0.1:3000"):
            with self.subTest(host=host):
                self.assertEqual(self.request("GET", "/recovery", headers={
                    "Host": host, "Authorization": credential})[0], 403)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        try:
            connection.putrequest("GET", "/recovery", skip_host=True)
            connection.putheader("Host", f"127.0.0.1:{self.port}")
            connection.putheader("Host", f"8.8.8.8:{self.port}")
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()

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
