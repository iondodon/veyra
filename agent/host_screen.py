"""On-demand, in-memory desktop capture. No images enter chat or persistent state."""
import os
import shutil
import subprocess
import threading
from host_browser import DESKTOP_VARIABLES


class ScreenUnavailable(Exception):
    pass


class HostScreen:
    def __init__(self):
        self.lock = threading.Lock()

    def capture(self):
        if not self.lock.acquire(blocking=False):
            raise ScreenUnavailable("Another screen capture is running. Try again shortly.")
        try:
            env = {name: os.environ[name] for name in (*DESKTOP_VARIABLES, "PATH", "HOME", "LANG")
                   if name in os.environ}
            try:
                session = subprocess.run(['systemctl', '--user', 'show-environment'],
                    capture_output=True, text=True, timeout=2)
                if session.returncode == 0:
                    for line in session.stdout.splitlines():
                        name, separator, value = line.partition('=')
                        if separator and name in DESKTOP_VARIABLES and not env.get(name):
                            env[name] = value
            except (OSError, subprocess.TimeoutExpired):
                pass
            if env.get('WAYLAND_DISPLAY') and shutil.which('grim'):
                command = ['grim', '-s', '0.6', '-']
            elif env.get('DISPLAY') and shutil.which('import'):
                command = ['import', '-window', 'root', '-resize', '60%', 'png:-']
            else:
                raise ScreenUnavailable("No supported desktop capture is available. Log into the host desktop. Wayland needs grim; X11 needs ImageMagick import.")
            try:
                result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                raise ScreenUnavailable("Screen capture failed or timed out. Check the host desktop session.") from None
            if result.returncode or not result.stdout.startswith(b'\x89PNG\r\n\x1a\n') or len(result.stdout) > 20 * 1024 * 1024:
                raise ScreenUnavailable("Cannot capture this desktop. It may be locked or screen capture may not be supported.")
            return result.stdout
        finally:
            self.lock.release()
