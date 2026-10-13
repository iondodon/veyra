import http.client
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_progress import TaskProgress
from web_chat import WebChat


class ProgressTests(unittest.TestCase):
    def test_lifecycle_and_approval(self):
        p = TaskProgress()
        p.start('Build this')
        p.event({'type': 'approval', 'text': '$ test'})
        p.event({'type': 'done'})
        self.assertEqual(p.snapshot()[0]['state'], 'approval')
        p.start('Approve these commands.', continuation=True)
        p.event({'type': 'clear_approval'})
        p.event({'type': 'message', 'role': 'tool', 'kind': 'command', 'text': '$ test'})
        p.event({'type': 'message', 'role': 'tool', 'kind': 'output', 'text': 'Exit code 1.'})
        p.event({'type': 'done'})
        task = p.snapshot()[0]
        self.assertEqual(task['state'], 'finished')
        self.assertEqual(task['request'], 'Build this')
        self.assertIn('Exit code 1.', [e['text'] for e in task['events']])
        self.assertEqual(len(p.tasks), 1)

    def test_bounds_copy_and_interruption(self):
        p = TaskProgress()
        for i in range(12):
            p.start(str(i))
        for i in range(110):
            p.add('output', 'x' * 9000)
        self.assertEqual(len(p.tasks), 10)
        self.assertEqual(len(p.tasks[0]['events']), 100)
        self.assertEqual(len(p.tasks[0]['events'][0]['text']), 8000)
        snapshot = p.snapshot(); snapshot.clear()
        self.assertEqual(len(p.tasks), 10)
        p.interrupt()
        self.assertEqual(p.tasks[0]['state'], 'interrupted')

    def test_web_routes_events_and_restart(self):
        with tempfile.TemporaryDirectory() as root:
            chat = WebChat(root)
            port = chat.start(port=0)
            try:
                chat.agent_event({'type': 'ready'})
                self.assertEqual(chat.submit({'text': 'Run tests'})[0], 202)
                chat.agent_event({'type': 'approval', 'approval_id': 'a', 'text': '$ test'})
                chat.agent_event({'type': 'done'})
                for path in ['/tasks', '/tasks.js', '/tasks.css', '/api/tasks']:
                    conn = http.client.HTTPConnection('127.0.0.1', port)
                    conn.request('GET', path)
                    response = conn.getresponse(); body = response.read()
                    self.assertEqual(response.status, 200)
                    if path == '/tasks':
                        self.assertIn(b'/app-updates.js', body)
                    if path == '/api/tasks':
                        self.assertEqual(json.loads(body)['tasks'][0]['state'], 'approval')
                    conn.close()
                chat.detach()
                self.assertEqual(chat.progress.snapshot()[0]['state'], 'interrupted')
                chat.begin_session()
                self.assertEqual(chat.progress.snapshot(), [])
            finally:
                chat.close()
