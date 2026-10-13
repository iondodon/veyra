import http.client
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recovery_server import RecoveryServer

class LocalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = RecoveryServer(self.tmp.name)
        self.port = self.server.start(0)
        self.addCleanup(self.server.close)
    def request(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, data
    def test_loopback_no_auth_and_no_gateway(self):
        self.assertEqual(self.server.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/api/messages")[0], 404)
        self.assertFalse(hasattr(self.server, "forward"))
    def test_rebinding_and_cross_origin_rejected(self):
        self.assertEqual(self.request("GET", "/", {"Host": "evil.test"})[0], 403)
        self.assertEqual(self.request("POST", "/api/recovery/action", {"Origin": "http://evil.test"}, '{}')[0], 403)
    def test_retry_same_origin_without_login(self):
        headers = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/recovery/action", headers, '{"action":"retry"}')[0], 202)
        self.assertEqual(self.server.recovery.take_request(), {"action": "retry"})
