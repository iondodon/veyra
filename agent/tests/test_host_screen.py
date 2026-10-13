import os
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host_screen import HostScreen, ScreenUnavailable

PNG = b'\x89PNG\r\n\x1a\nexample'

class HostScreenTests(unittest.TestCase):
    def test_wayland_capture_and_session_discovery(self):
        results = [SimpleNamespace(returncode=0, stdout='WAYLAND_DISPLAY=wayland-1\nXDG_RUNTIME_DIR=/run/user/1000\nPRIVATE_SECRET=not-imported'), SimpleNamespace(returncode=0, stdout=PNG)]
        with patch.dict(os.environ, {}, clear=True), patch('host_screen.shutil.which', return_value='/usr/bin/grim'), patch('host_screen.subprocess.run', side_effect=results) as run:
            self.assertEqual(HostScreen().capture(), PNG)
            args, kwargs = run.call_args
            self.assertEqual(args[0], ['grim', '-s', '0.6', '-'])
            self.assertNotIn('PRIVATE_SECRET', kwargs['env'])

    def test_x11_capture(self):
        with patch.dict(os.environ, {'DISPLAY': ':0'}, clear=True), patch('host_screen.shutil.which', return_value='/usr/bin/import'), patch('host_screen.subprocess.run', side_effect=[OSError(), SimpleNamespace(returncode=0, stdout=PNG)]) as run:
            self.assertEqual(HostScreen().capture(), PNG)
            self.assertEqual(run.call_args.args[0][0], 'import')

    def test_missing_desktop(self):
        with patch.dict(os.environ, {}, clear=True), patch('host_screen.subprocess.run', side_effect=OSError()):
            with self.assertRaises(ScreenUnavailable): HostScreen().capture()

    def test_failure_timeout_and_lock_released(self):
        for result in (SimpleNamespace(returncode=1, stdout=b''), SimpleNamespace(returncode=0, stdout=b'bad'), subprocess.TimeoutExpired('grim', 8)):
            screen = HostScreen()
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-1'}, clear=True), patch('host_screen.shutil.which', return_value='grim'), patch('host_screen.subprocess.run', side_effect=[OSError(), result]):
                with self.assertRaises(ScreenUnavailable): screen.capture()
            self.assertFalse(screen.lock.locked())

    def test_concurrent_capture_rejected(self):
        screen = HostScreen()
        screen.lock.acquire()
        with self.assertRaises(ScreenUnavailable): screen.capture()
        screen.lock.release()
