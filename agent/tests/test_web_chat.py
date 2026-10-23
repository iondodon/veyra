import base64
import http.client
import json
import sqlite3
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

    def test_admin_routes(self):
        status, page = self.request('GET', '/admin')
        self.assertEqual(status, 200)
        self.assertIn(b'/app-updates.js', page)
        self.assertEqual(self.request('GET', '/api/admin')[0], 200)
        self.assertEqual(self.request('POST', '/api/admin', {'command': 'id'})[0], 400)
        remote = {'Host': f'8.8.8.8:{self.port}',
                  'Origin': f'http://8.8.8.8:{self.port}',
                  'Authorization': 'Basic ' + base64.b64encode(b'veyra:veyra').decode()}
        self.assertEqual(self.request('GET', '/api/admin', headers=remote)[0], 403)
        self.assertEqual(self.request('POST', '/api/admin', {'action': 'firewall'}, remote)[0], 403)

    def test_markdown_assets_are_served_and_versioned(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        for filename in ("marked.js", "purify.js", "chat-markdown.js"):
            self.assertIn(f'src="/{filename}"'.encode(), page)
            status, content = self.request("GET", "/" + filename)
            self.assertEqual(status, 200)
            self.assertEqual(content, self.chat.assets[filename])
        self.assertLess(page.index(b'/marked.js'), page.index(b'/chat-markdown.js'))
        self.assertLess(page.index(b'/chat-markdown.js'), page.index(b'/chat.js'))
        self.assertIn(b'/app-updates.js', page)

    def test_initiative_removed_even_with_saved_enabled_setting(self):
        with sqlite3.connect(self.chat.database) as db:
            db.execute("INSERT OR REPLACE INTO settings(name,value) VALUES ('initiative', ?)",
                       (json.dumps({'enabled': True}),))
        restarted = WebChat(self.root)
        self.assertNotIn('initiative', restarted.snapshot(0)['status'])
        self.assertFalse(hasattr(restarted, 'initiative'))
        for endpoint in ('/api/initiative', '/api/check-in'):
            self.assertEqual(self.request('POST', endpoint, {'manual': True, 'available': True})[0], 404)
        self.assertTrue(self.chat.inbox.empty())
        status, page = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertNotIn(b'initiative-', page)
        self.assertNotIn(b'/api/check-in', self.chat.assets['chat.js'])
        self.assertIn(b'/app-updates.js', page)

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

    def prepare_auto_approval(self):
        self.chat.agent_event({"type": "ready"})
        self.chat.agent_event({"type": "configuration", "provider": "chatgpt", "connected": True})

    def toggle_auto(self, enabled):
        return self.request("POST", "/api/auto-approve", {"enabled": enabled, "session_id": self.chat.session_id})

    def test_auto_approval_default_validation_and_http_protection(self):
        self.prepare_auto_approval()
        self.assertFalse(self.chat.snapshot(0)['status']['auto_approve'])
        self.assertEqual(self.request('POST', '/api/auto-approve', {'enabled': 'yes'})[0], 400)
        self.assertEqual(self.request('POST', '/api/auto-approve', {'enabled': True, 'session_id': 'old'})[0], 409)
        self.assertEqual(self.request('POST', '/api/auto-approve', {}, {'Origin': 'http://evil.test'})[0], 403)
        self.assertEqual(self.toggle_auto(True)[0], 200)
        self.chat.detach()
        self.assertFalse(self.chat.auto_approve)
        self.chat.begin_session()
        self.assertFalse(self.chat.auto_approve)

    def test_auto_approval_new_requests_only_and_one_delivery(self):
        self.prepare_auto_approval()
        self.assertEqual(self.toggle_auto(True)[0], 200)
        self.chat.busy = True
        self.chat.agent_event({'type': 'approval', 'approval_id': 'a', 'text': '$ printf hello'})
        self.assertTrue(self.chat.inbox.empty())
        self.chat.agent_event({'type': 'done'})
        message = self.chat.inbox.get_nowait()
        self.assertEqual((message['text'], message['approval_id']), ('/approve', 'a'))
        self.assertTrue(any('Automatically approving' in e['text'] for e in self.chat.snapshot(0)['events']))
        self.chat.agent_event({'type': 'done'})
        self.assertTrue(self.chat.inbox.empty())
        self.chat.agent_event({'type': 'clear_approval'})
        self.toggle_auto(False)
        self.chat.agent_event({'type': 'approval', 'approval_id': 'b', 'text': '$ test'})
        self.chat.agent_event({'type': 'done'})
        self.assertTrue(self.chat.inbox.empty())
        self.assertEqual(self.toggle_auto(True)[0], 409)

    def test_turn_off_before_done_and_disconnect_cancel_auto(self):
        self.prepare_auto_approval()
        self.toggle_auto(True)
        self.chat.agent_event({'type': 'approval', 'approval_id': 'a', 'text': '$ test'})
        self.assertEqual(self.toggle_auto(False)[0], 200)
        self.chat.agent_event({'type': 'done'})
        self.assertTrue(self.chat.inbox.empty())
        self.chat.agent_event({'type': 'clear_approval'})
        self.toggle_auto(True)
        self.chat.agent_event({'type': 'configuration', 'connected': False})
        self.assertFalse(self.chat.auto_approve)
        self.assertEqual(self.toggle_auto(True)[0], 409)
        self.prepare_auto_approval()
        self.toggle_auto(True)
        self.chat.agent_event({'type': 'configuration', 'provider': 'claude', 'connected': True})
        self.assertFalse(self.chat.auto_approve)

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
        self.assertEqual([e["text"] for e in events], ["hello", "Reply: hello"])
        self.assertEqual(events[0]["role"], "user")

    def test_versions_removed_and_remaining_pages_keep_shared_updates(self):
        for path in ("/versions", "/versions.js", "/versions.css", "/api/versions",
                     "/api/versions?offset=0"):
            self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("POST", "/api/versions", {})[0], 404)
        self.assertFalse(hasattr(self.chat, "timeline"))
        for path in ("/", "/dashboard"):
            status, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertNotIn(b'/versions', body)
            self.assertIn(b'/app-updates.js', body)
            self.assertIn(self.chat.ui_version.encode(), body)

    def test_mobile_app_removed_from_routes_and_navigation(self):
        for path in ("/mobile", "/mobile.html"):
            self.assertEqual(self.request("GET", path)[0], 404)
        for path in ("/", "/dashboard"):
            status, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertNotIn(b'href="/mobile"', body)
            self.assertNotIn(b'iPhone app', body)
            self.assertIn(b'/app-updates.js', body)

    def test_dashboard_is_read_only_and_excludes_private_configuration(self):
        self.chat.configuration.update(provider="chatgpt", model="test-model", connected=True,
                                       sign_in_url="https://private-signin.example/secret")
        self.chat.approval_id = "private-approval-id"
        status, body = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["agent"]["model"], "test-model")
        self.assertTrue(data["agent"]["approval_pending"])
        self.assertNotIn(b"private-signin", body)
        self.assertNotIn(b"private-approval-id", body)
        self.assertNotIn(b"network_password", body)
        self.assertEqual(self.request("POST", "/api/dashboard", {})[0], 404)
        for path in ("/dashboard", "/dashboard.js", "/dashboard.css"):
            self.assertEqual(self.request("GET", path)[0], 200)
        with patch.object(self.chat.server.RequestHandlerClass, "local_client", return_value=False):
            self.assertEqual(self.request("GET", "/api/dashboard")[0], 401)
            credential = "Basic " + base64.b64encode(b"veyra:veyra").decode()
            self.assertEqual(self.request("GET", "/api/dashboard", headers={"Authorization": credential})[0], 200)

    def test_initial_configuration_has_no_service_or_model(self):
        status, data = self.request("GET", "/api/events")
        self.assertEqual(status, 200)
        configuration = json.loads(data)["status"]["configuration"]
        self.assertIsNone(configuration["provider"])
        self.assertIsNone(configuration["model"])
        self.assertIsNone(configuration["sign_in_url"])
        self.assertFalse(configuration["connected"])

    def test_signin_links_are_only_returned_to_local_clients_for_both_subscriptions(self):
        credential = "Basic " + base64.b64encode(b"veyra:veyra").decode()
        for service, url in (("chatgpt", "https://auth.openai.com/fixture"),
                             ("claude", "https://claude.com/cai/oauth/authorize?state=fixture")):
            with self.subTest(service=service):
                self.chat.agent_event({"type": "configuration", "provider": service, "connected": False})
                self.chat.agent_event({"type": "sign_in", "provider": service, "url": url})
                status, body = self.request("GET", "/api/events")
                local = json.loads(body)["status"]
                self.assertTrue(local["local_client"])
                self.assertEqual(local["configuration"]["sign_in_url"], url)
                with patch.object(self.chat.server.RequestHandlerClass, "local_client", return_value=False):
                    status, body = self.request("GET", "/api/events", headers={"Authorization": credential})
                remote = json.loads(body)["status"]
                self.assertEqual(status, 200)
                self.assertFalse(remote["local_client"])
                self.assertIsNone(remote["configuration"]["sign_in_url"])
                self.assertEqual(self.chat.configuration["sign_in_url"], url)

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
        self.assertFalse(any(e.get("approval_id") == "old" for e in self.chat.snapshot(0)["events"]))

    def test_restart_shows_a_clean_session_and_keeps_previous_messages_saved(self):
        self.chat.publish("First message", role="assistant")
        first = self.chat.snapshot(0)
        restarted = WebChat(self.root)
        self.assertEqual(restarted.snapshot(0)["events"], [])
        self.assertNotEqual(restarted.session_id, self.chat.session_id)
        connection = sqlite3.connect(restarted.database)
        self.addCleanup(connection.close)
        saved = [json.loads(row[0])["text"] for row in connection.execute("SELECT payload FROM events")]
        self.assertIn("First message", saved)
        restarted.publish("Second message")
        later = restarted.snapshot(first["cursor"])
        self.assertEqual([e["text"] for e in later["events"]], ["Second message"])
        self.assertFalse(later["status"]["ready"])
        self.assertEqual(restarted.snapshot(later["cursor"])["events"], [])

    def test_version_handoff_survives_restart_and_is_announced_only_when_ready(self):
        (self.root / "agent").mkdir()
        notes = {"summary": "Added a dashboard.", "instructions": "Use Dashboard at the top to see activity.",
                 "links": [{"label": "Open dashboard", "path": "/dashboard"}]}
        (self.root / "agent/release_notes.json").write_text(json.dumps(notes))
        for _ in range(2):
            restarted = WebChat(self.root)
            self.assertEqual(restarted.snapshot(0)["events"], [])
            restarted.agent_event({"type": "ready"})
            restarted.agent_event({"type": "ready"})
            events = restarted.snapshot(0)["events"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["kind"], "update")
            self.assertIn(notes["summary"], events[0]["text"])
            self.assertIn(notes["instructions"], events[0]["text"])
            self.assertEqual(events[0]["links"], notes["links"])

    def test_ui_version_matches_served_page_and_assets_are_fixed_for_the_session(self):
        version = self.chat.snapshot(0)["status"]["ui_version"]
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(('name="veyra-ui-version" content="' + version + '"').encode(), page)
        self.assertNotIn(b"{{UI_VERSION}}", page)
        self.assertIn(b'<script src="/app-updates.js" defer></script>', page)
        status, version_body = self.request("GET", "/api/app-version")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(version_body), {"ready": False, "ui_version": version})
        self.assertEqual(self.request("GET", "/app-updates.js")[0], 200)
        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("Reading a changed working tree")):
            self.assertEqual(self.request("GET", "/chat.js"), (200, self.chat.assets["chat.js"]))

    def test_every_restart_changes_version_even_when_all_assets_are_identical(self):
        restarted = WebChat(self.root)
        self.assertEqual(restarted.assets, self.chat.assets)
        self.assertNotEqual(restarted.ui_version, self.chat.ui_version)

    def test_html_responses_automatically_include_the_shared_update_monitor(self):
        for html in (b"<!doctype html><title>Another page</title><h1>Page</h1>",
                     b"<HTML><HEAD><title>Another page</title></HEAD><body>Page</body></HTML>"):
            with self.subTest(html=html):
                self.chat.assets["chat.html"] = html
                status, page = self.request("GET", "/")
                self.assertEqual(status, 200)
                self.assertIn(b'<script src="/app-updates.js" defer></script>', page)
                self.assertIn(self.chat.ui_version.encode(), page)

    def test_routine_logs_are_hidden_and_errors_remain_visible(self):
        self.chat.publish("starting agent", kind="status")
        self.chat.publish("Something needs your attention", kind="error")
        data = self.chat.snapshot(0)
        self.assertEqual([e["text"] for e in data["events"]], ["Something needs your attention"])
        self.assertEqual(self.chat.snapshot(data["cursor"])["events"], [])

    def test_background_signin_links_do_not_replace_selected_service(self):
        self.chat.agent_event({"type": "configuration", "provider": "claude", "connected": False})
        self.chat.agent_event({"type": "sign_in", "provider": "chatgpt", "url": "https://auth.openai.com/old"})
        self.assertIsNone(self.chat.configuration["sign_in_url"])
        self.chat.agent_event({"type": "sign_in", "provider": "claude", "url": "https://claude.com/cai/oauth/authorize?state=fixture"})
        self.assertTrue(self.chat.configuration["sign_in_url"].startswith("https://claude.com/"))
        self.chat.agent_event({"type": "configuration", "provider": "openai", "sign_in_url": None})
        self.chat.agent_event({"type": "sign_in", "provider": "claude", "url": "https://claude.com/stale"})
        self.assertIsNone(self.chat.configuration["sign_in_url"])

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
            for path in ("/", "/chat.js", "/app-updates.js", "/api/app-version", "/api/events", "/api/settings"):
                self.assertEqual(self.request("GET", path)[0], 401)
                self.assertEqual(self.request("GET", path, headers={"Authorization": "Basic wrong"})[0], 401)
                self.assertEqual(self.request("GET", path, headers={"Authorization": credential})[0], 200)
            self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"})[0], 401)
            self.assertFalse(self.chat.busy)
            self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"},
                             {"Authorization": credential, "Origin": "https://attacker.example"})[0], 403)
            self.assertEqual(self.request("POST", "/api/messages", {"text": "hello"},
                             {"Authorization": credential})[0], 202)

    def test_remembered_browser_survives_restart_and_password_revokes(self):
        handler = self.chat.server.RequestHandlerClass
        credential = "Basic " + base64.b64encode(b"veyra:veyra").decode()
        with patch.object(handler, "local_client", return_value=False):
            c = http.client.HTTPConnection("127.0.0.1", self.port)
            c.request("GET", "/", headers={"Authorization": credential})
            response = c.getresponse()
            self.assertEqual(response.status, 200)
            cookie = response.getheader("Set-Cookie")
            response.read()
            c.close()
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            self.assertIn("Max-Age=15552000", cookie)
            cookie = cookie.split(";", 1)[0]
            self.assertEqual(self.request("GET", "/", headers={"Cookie": cookie})[0], 200)
            self.assertEqual(self.request("GET", "/", headers={"Cookie": cookie + "bad"})[0], 401)
            self.assertEqual(self.request("POST", "/api/password", {},
                             {"Cookie": cookie, "Origin": "https://attacker.example"})[0], 403)
        restarted = WebChat(self.root)
        port = restarted.start(0)
        self.addCleanup(restarted.close)
        with patch.object(restarted.server.RequestHandlerClass, "local_client", return_value=False):
            def check():
                c = http.client.HTTPConnection("127.0.0.1", port)
                c.request("GET", "/", headers={"Cookie": cookie})
                r = c.getresponse(); r.read(); c.close()
                return r.status
            self.assertEqual(check(), 200)
            self.assertFalse(restarted.valid_browser_token("1.nonce.invalid"))
            token = restarted.browser_token()
            with patch("web_chat.time.time", return_value=time.time() + 181 * 86400):
                self.assertFalse(restarted.valid_browser_token(token))
            self.assertEqual(restarted.change_password({"current_password": "veyra",
                                                       "new_password": "new-test-password"})[0], 200)
            self.assertEqual(check(), 401)

    def test_default_password_and_reminder_survive_restart(self):
        restarted = WebChat(self.root)
        self.assertEqual(self.chat.network_password, "veyra")
        self.assertEqual(restarted.network_password, "veyra")
        self.assertTrue(restarted.snapshot(0)["status"]["password_is_default"])
        self.assertNotIn(self.chat.network_password, json.dumps(self.chat.snapshot(0)))
        self.assertEqual(self.chat.database.stat().st_mode & 0o777, 0o600)

    def test_private_key_form_saves_keys_durably_without_exposing_them(self):
        key = "sk-fixture-private-api-key"
        status, body = self.request("POST", "/api/keys", {"provider": "openai", "key": key})
        self.assertEqual(status, 200)
        self.assertNotIn(key, body.decode())
        restarted = WebChat(self.root)
        self.assertEqual(restarted.credentials.environment({})["OPENAI_API_KEY"], key)
        self.assertEqual(restarted.credentials.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(restarted.credentials.directory.stat().st_mode & 0o777, 0o700)
        status, body = self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["can_save_keys"])
        self.assertTrue(next(item for item in json.loads(body)["providers"] if item["provider"] == "openai")["configured"])
        self.assertNotIn(key, body.decode())
        self.assertNotIn(key, json.dumps(self.chat.snapshot(0)))
        self.assertNotIn(key.encode(), self.chat.database.read_bytes())

    def test_keys_cannot_be_submitted_over_lan_or_from_another_origin(self):
        payload = {"provider": "anthropic", "key": "sk-ant-fixture-private-key"}
        handler = self.chat.server.RequestHandlerClass
        credential = "Basic " + base64.b64encode(b"veyra:veyra").decode()
        with patch.object(handler, "local_client", return_value=False):
            self.assertEqual(self.request("POST", "/api/keys", payload)[0], 401)
            self.assertEqual(self.request("POST", "/api/keys", payload, {"Authorization": credential})[0], 403)
            status, body = self.request("GET", "/api/settings", headers={"Authorization": credential})
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)["can_save_keys"])
        self.assertEqual(self.request("POST", "/api/keys", payload, {"Origin": "https://attacker.example"})[0], 403)
        self.assertFalse(self.chat.credentials.path.exists())

    def test_keys_pasted_into_chat_are_rejected_before_storage_or_model_submission(self):
        self.attach_agent()
        key = "sk-fixture-should-never-reach-model"
        status, body = self.request("POST", "/api/messages", {"text": "Here is my key: " + key})
        self.assertEqual(status, 400)
        self.assertTrue(json.loads(body)["open_api_keys"])
        self.assertFalse(self.chat.busy)
        self.assertNotIn(key.encode(), self.chat.database.read_bytes())
        self.chat.credentials.save("openai", "private-key-without-known-prefix")
        self.assertEqual(self.request("POST", "/api/messages", {"text": "private-key-without-known-prefix"})[0], 400)
        self.chat.publish("An error containing " + key + " and private-key-without-known-prefix", kind="error")
        text = json.dumps(self.chat.snapshot(0))
        self.assertNotIn(key, text)
        self.assertNotIn("private-key-without-known-prefix", text)
        self.assertIn("API key hidden", text)

    def test_invalid_keys_and_updates_during_pending_work_are_rejected(self):
        for payload in ([], {}, {"provider": [], "key": "valid-fixture-key"},
                        {"provider": "unknown", "key": "valid-fixture-key"},
                        {"provider": "openai", "key": "short"},
                        {"provider": "openai", "key": "a key with spaces"}):
            self.assertEqual(self.request("POST", "/api/keys", payload)[0], 400)
        self.chat.busy = True
        self.assertEqual(self.request("POST", "/api/keys", {"provider": "openai", "key": "valid-fixture-key"})[0], 409)
        self.chat.busy = False
        self.chat.approval_id = "pending"
        self.assertEqual(self.request("POST", "/api/keys", {"provider": "openai", "key": "valid-fixture-key"})[0], 409)
        self.assertFalse(self.chat.credentials.path.exists())

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

class DirectNetworkBoundaryTests(unittest.TestCase):
    def test_numeric_public_host_requires_auth_and_cannot_spoof_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = WebChat(Path(tmp))
            port = chat.start(0)
            try:
                def request(method, path, authenticated=False):
                    c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                    host = '8.8.8.8:8080'
                    headers = {'Host': host, 'Origin': 'http://' + host, 'Content-Type': 'application/json',
                               'X-Veyra-Local': '1', 'X-Veyra-Token': 'anything'}
                    if authenticated:
                        headers['Authorization'] = 'Basic ' + base64.b64encode(b'veyra:veyra').decode()
                    c.request(method, path, body='{}' if method == 'POST' else None, headers=headers)
                    res = c.getresponse(); body = json.loads(res.read()); c.close()
                    return res.status, body
                self.assertEqual(request('GET', '/api/events')[0], 401)
                status, data = request('GET', '/api/events', True)
                self.assertEqual(status, 200)
                self.assertFalse(data['status']['local_client'])
                self.assertEqual(request('POST', '/api/keys', True)[0], 403)
                self.assertEqual(request('GET', '/recovery', True)[0], 404)
            finally:
                chat.close()
