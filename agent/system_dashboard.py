"""Read-only, allowlisted host metrics. Never inspect credentials or environment."""

import os
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time


class SystemDashboard:
    def __init__(self, root, proc=Path('/proc')):
        self.root = Path(root)
        self.proc = Path(proc)
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.cached = None
        self.cached_at = 0

    def git(self, *args):
        try:
            result = subprocess.run(['git', '--no-optional-locks', *args], cwd=self.root, capture_output=True,
                                    text=True, timeout=2, check=True)
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    def collect(self):
        with self.lock:
            now = time.monotonic()
            if self.cached is not None and now - self.cached_at < 5:
                return self.cached
            uptime = None
            memory = None
            disk = None
            load = None
            try:
                uptime = float((self.proc / 'uptime').read_text().split()[0])
            except (OSError, ValueError, IndexError):
                pass
            try:
                values = {}
                for line in (self.proc / 'meminfo').read_text().splitlines():
                    name, value = line.split(':', 1)
                    if name in ('MemTotal', 'MemAvailable'):
                        values[name] = int(value.split()[0]) * 1024
                total, available = values['MemTotal'], values['MemAvailable']
                if total > 0 and 0 <= available <= total:
                    memory = {'total_bytes': total, 'used_bytes': total - available}
            except (OSError, ValueError, KeyError, IndexError):
                pass
            try:
                total, used, free = shutil.disk_usage(self.root)
                disk = {'total_bytes': total, 'used_bytes': used, 'free_bytes': free}
            except OSError:
                pass
            try:
                load = list(os.getloadavg())
            except (AttributeError, OSError):
                pass
            branch = self.git('symbolic-ref', '--quiet', '--short', 'HEAD')
            commit = self.git('rev-parse', '--short=12', 'HEAD')
            changes = self.git('status', '--porcelain', '--untracked-files=normal')
            self.cached = {
                'sampled_at': time.time(), 'os': platform.system(),
                'cpu_count': os.cpu_count(), 'load_average': load,
                'host_uptime_seconds': uptime, 'memory': memory, 'disk': disk,
                'version': {'branch': branch, 'commit': commit,
                            'dirty': None if changes is None else bool(changes)},
            }
            self.cached_at = now
            return self.cached

    def snapshot(self, chat):
        data = dict(self.collect())
        with chat.lock:
            # Deliberately exclude sign-in URLs, credentials, approval contents and history.
            data['agent'] = {
                'uptime_seconds': time.monotonic() - self.started,
                'ready': chat.ready, 'busy': chat.busy,
                'approval_pending': bool(chat.approval_id),
                'provider': chat.configuration.get('provider'),
                'model': chat.configuration.get('model'),
                'service_connected': bool(chat.configuration.get('connected')),
            }
        return data
