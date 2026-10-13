"""Removed task tracking must not survive through stale pages or requests."""
import http.client
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_chat import WebChat


class NoTasksTests(unittest.TestCase):
    def test_removed_routes_and_chat_lifecycle(self):
        with tempfile.TemporaryDirectory() as root:
            chat = WebChat(root)
            port = chat.start(port=0)
            try:
                for path in ['/tasks', '/tasks.js', '/tasks.css', '/api/tasks']:
                    with self.subTest(path=path):
                        conn = http.client.HTTPConnection('127.0.0.1', port)
                        conn.request('GET', path)
                        response = conn.getresponse()
                        response.read()
                        self.assertEqual(response.status, 404)
                        conn.close()
                for path in ['/', '/dashboard']:
                    conn = http.client.HTTPConnection('127.0.0.1', port)
                    conn.request('GET', path)
                    response = conn.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertNotIn(b'href="/tasks"', body)
                    self.assertIn(b'/app-updates.js', body)
                    conn.close()
                chat.agent_event({'type': 'ready'})
                self.assertEqual(chat.submit({'text': 'Run tests'})[0], 202)
                chat.agent_event({'type': 'approval', 'approval_id': 'a', 'text': '$ test'})
                chat.agent_event({'type': 'done'})
                self.assertEqual(chat.approval_id, 'a')
                self.assertEqual(chat.submit({'text': '/approve', 'approval_id': 'a'})[0], 202)
                chat.agent_event({'type': 'clear_approval'})
                chat.agent_event({'type': 'message', 'text': 'Finished'})
                chat.agent_event({'type': 'done'})
                self.assertFalse(chat.busy)
                self.assertIsNone(chat.approval_id)
                self.assertFalse(hasattr(chat, 'progress'))
                chat.detach()
                chat.begin_session()
            finally:
                chat.close()
