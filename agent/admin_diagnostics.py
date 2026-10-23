"""One read-only privileged diagnostic, authenticated by the host's polkit agent.

No password API, shell, persistent privilege grant, or arbitrary command input.
"""
import os
from pathlib import Path
import subprocess
import threading


class AdminDiagnostics:
    def __init__(self):
        self.lock = threading.Lock()
        self.result = {"running": False, "message": "Not run yet.", "output": ""}

    def snapshot(self):
        with self.lock:
            return dict(self.result)

    def start(self, payload):
        if payload != {"action": "firewall"}:
            return 400, {"error": "Only the fixed read-only firewall inspection is supported."}
        with self.lock:
            if self.result["running"]:
                return 409, {"error": "A diagnostic is already running."}
            self.result = {"running": True, "message": "Approve the system authentication prompt on the Veyra PC. No prompt? Cancel or wait up to 90 seconds.", "output": ""}
        threading.Thread(target=self._run, daemon=True).start()
        return 202, self.snapshot()

    def _run(self):
        try:
            # Use only root-owned system executables, never a workspace helper.
            pkexec = next((p for p in ("/usr/bin/pkexec",) if Path(p).is_file()), None)
            nft = next((p for p in ("/usr/sbin/nft", "/usr/bin/nft") if Path(p).is_file()), None)
            if not pkexec or not nft:
                raise OSError("This host needs polkit (pkexec), nftables, and a desktop authentication agent.")
            env = {k: os.environ[k] for k in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "LANG") if k in os.environ}
            env["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
            proc = subprocess.Popen([pkexec, "--disable-internal-agent", nft, "list", "ruleset"],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, env=env)
            try:
                stdout, stderr = proc.communicate(timeout=90)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise OSError("Authentication or inspection timed out. Retry from the PC desktop.")
            result = {"running": False, "message": "Firewall rules read; no settings changed." if proc.returncode == 0 else "Inspection failed or authentication was cancelled. No settings changed.",
                      "output": (stdout + stderr)[:100000]}
        except OSError as exc:
            result = {"running": False, "message": str(exc), "output": ""}
        with self.lock:
            self.result = result
