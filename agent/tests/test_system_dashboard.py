import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from system_dashboard import SystemDashboard


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.dashboard = SystemDashboard(self.root, self.proc)

    def test_memory_uptime_and_version(self):
        (self.proc / 'meminfo').write_text('MemTotal: 1000 kB\nMemAvailable: 400 kB\n')
        (self.proc / 'uptime').write_text('1234.5 999\n')
        with patch.object(self.dashboard, 'git', side_effect=['local', 'abc123', ' M agent/chat.html']):
            data = self.dashboard.collect()
        self.assertEqual(data['memory'], {'total_bytes': 1024000, 'used_bytes': 614400})
        self.assertEqual(data['host_uptime_seconds'], 1234.5)
        self.assertEqual(data['version'], {'branch': 'local', 'commit': 'abc123', 'dirty': True})
        self.assertNotIn('agent/chat.html', json.dumps(data))

    def test_unavailable_metrics_and_cache(self):
        with patch.object(self.dashboard, 'git', return_value=None) as git, patch('system_dashboard.shutil.disk_usage', side_effect=OSError), patch('system_dashboard.os.getloadavg', side_effect=OSError):
            first = self.dashboard.collect()
            second = self.dashboard.collect()
        self.assertIs(first, second)
        self.assertEqual(git.call_count, 3)
        for key in ('memory', 'host_uptime_seconds', 'disk', 'load_average'):
            self.assertIsNone(first[key])
        self.assertIsNone(first['version']['dirty'])

    def test_git_timeout_is_unavailable(self):
        with patch('system_dashboard.subprocess.run', side_effect=subprocess.TimeoutExpired('git', 2)):
            self.assertIsNone(self.dashboard.git('status'))

    def test_invalid_memory_is_unavailable(self):
        (self.proc / 'meminfo').write_text('MemTotal: 100 kB\nMemAvailable: 200 kB\n')
        with patch.object(self.dashboard, 'git', return_value=None):
            self.assertIsNone(self.dashboard.collect()['memory'])
