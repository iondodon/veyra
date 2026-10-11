"""Open subscription sign-in in the Veyra host's desktop session."""

import os
from pathlib import Path
import subprocess
import sys

DESKTOP_VARIABLES = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
                     "XDG_RUNTIME_DIR", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE")
BROWSER_VARIABLES = (*DESKTOP_VARIABLES, "PATH", "HOME", "USER", "LOGNAME", "SHELL", "BROWSER",
                     "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                     "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
                     "SSL_CERT_FILE", "SSL_CERT_DIR")


def open_sign_in(url):
    env = {name: os.environ[name] for name in BROWSER_VARIABLES if name in os.environ}
    if sys.platform.startswith("linux") and not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        # A background service may have started before the desktop session.
        # Import only desktop connection details, never credentials or API keys.
        try:
            result = subprocess.run(["systemctl", "--user", "show-environment"],
                capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    name, separator, value = line.partition("=")
                    if separator and name in DESKTOP_VARIABLES and not env.get(name):
                        env[name] = value
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
            return False
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), url], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


if __name__ == "__main__":
    import webbrowser
    try:
        opened = len(sys.argv) == 2 and webbrowser.open(sys.argv[1], new=2)
    except (webbrowser.Error, OSError):
        opened = False
    raise SystemExit(0 if opened else 1)
