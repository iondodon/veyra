import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from version_timeline import VersionTimeline, HistoryUnavailable


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, check=True, text=True).stdout.strip()

    def commit(self, title, notes=None):
        (self.root / 'agent').mkdir(exist_ok=True)
        (self.root / 'agent/release_notes.json').write_text(json.dumps(notes or {'summary': title, 'instructions': 'Try it.', 'links': [{'label': 'Chat', 'path': '/'}]}))
        self.git('add', '.')
        self.git('commit', '-qm', title)
        return self.git('rev-parse', 'HEAD')

    def test_order_pagination_notes_and_running_snapshot(self):
        old = self.commit('First')
        current = self.commit('Second <script>')
        timeline = VersionTimeline(self.root)
        timeline.PAGE_SIZE = 1
        self.commit('Later commit')
        (self.root / 'agent/release_notes.json').write_text('private uncommitted draft')
        first = timeline.page()
        self.assertEqual(first['current'], current)
        self.assertEqual(first['versions'][0]['commit'], current)
        self.assertTrue(first['versions'][0]['current'])
        self.assertEqual(first['versions'][0]['notes']['summary'], 'Second <script>')
        self.assertEqual(first['next_offset'], 1)
        second = timeline.page(1)
        self.assertEqual(second['versions'][0]['commit'], old)
        self.assertFalse(second['versions'][0]['current'])
        self.assertIsNone(second['next_offset'])
        self.assertEqual(timeline.page(2)['versions'], [])

    def test_missing_and_invalid_notes(self):
        self.git('commit', '--allow-empty', '-qm', 'No notes')
        self.commit('Unsafe notes', {'summary': 'Unsafe', 'instructions': 'Test', 'links': [{'label': 'No', 'path': '//evil.invalid'}]})
        self.assertTrue(all(row['notes'] is None for row in VersionTimeline(self.root).page()['versions']))

    def test_errors_and_cursor_validation(self):
        timeline = VersionTimeline(self.root)
        with self.assertRaises(HistoryUnavailable):
            timeline.page()
        for offset in (-1, 100001, 'bad'):
            with self.assertRaises(ValueError):
                timeline.page(offset)
        with patch('version_timeline.subprocess.run', side_effect=subprocess.TimeoutExpired('git', 3)):
            with self.assertRaises(HistoryUnavailable):
                timeline.git('log')
