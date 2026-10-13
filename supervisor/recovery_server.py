"""Loopback-only process recovery. No agent HTTP or application knowledge."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
from urllib.parse import urlsplit
from recovery import Recovery, RecoveryError

WEB_PORT = int(os.environ.get("VEYRA_RECOVERY_PORT", "3001"))
WEB_URL = f"http://127.0.0.1:{WEB_PORT}"
ASSETS = Path(__file__).resolve().parent

class RecoveryServer:
    def __init__(self, root):
        self.recovery = Recovery(root)
        self.process = None
        self.active_revision = None
        self.server = None

    def publish(self, text, *, kind="status"):
        self.recovery.publish(text, kind=kind)

    def attach(self, process, revision):
        self.process = process
        self.active_revision = revision
        threading.Thread(target=self.read_diagnostics, args=(process,), daemon=True).start()

    def read_diagnostics(self, process):
        try:
            for line in process.stdout:
                if line.strip():
                    self.publish(line.rstrip())
        finally:
            process.stdout.close()

    def detach(self):
        self.process = None
        self.active_revision = None

    def ready(self):
        return self.process is not None and self.process.poll() is None

    def start(self, port=WEB_PORT):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def setup(self):
                super().setup()
                self.connection.settimeout(10)
            def respond(self, status, data, content_type="application/json; charset=utf-8"):
                body = json.dumps(data).encode() if isinstance(data, dict) else data
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                self.end_headers()
                self.wfile.write(body)
            def allowed(self):
                hosts = self.headers.get_all("Host", [])
                return len(hosts) == 1 and hosts[0] in (f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}")
            def do_GET(self):
                if not self.allowed():
                    return self.respond(403, {"error": "Use the local recovery address."})
                path = urlsplit(self.path).path
                if path == "/api/recovery":
                    return self.respond(200, owner.recovery.status(owner.ready(), owner.active_revision))
                assets = {"/": ("recovery.html", "text/html"), "/recovery": ("recovery.html", "text/html"), "/recovery.css": ("recovery.css", "text/css"), "/recovery.js": ("recovery.js", "text/javascript")}
                if path not in assets:
                    return self.respond(404, {"error": "Page not found."})
                name, content_type = assets[path]
                return self.respond(200, (ASSETS / name).read_bytes(), content_type)
            def do_POST(self):
                if not self.allowed() or self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
                    return self.respond(403, {"error": "Use the local recovery page."})
                if self.path not in ("/api/recovery/preview", "/api/recovery/action"):
                    return self.respond(404, {"error": "Page not found."})
                if self.headers.get("Transfer-Encoding"):
                    return self.respond(400, {"error": "Use Content-Length."})
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.respond(415, {"error": "Send JSON."})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 100000:
                        return self.respond(413, {"error": "Message too large."})
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("Choose a recovery action.")
                    if self.path.endswith("/preview"):
                        return self.respond(200, owner.recovery.preview(payload.get("action")))
                    owner.recovery.request(payload)
                    return self.respond(202, {"ok": True})
                except (ValueError, RecoveryError, OSError, subprocess.TimeoutExpired) as exc:
                    return self.respond(409, {"error": str(exc)})
        class Server(ThreadingHTTPServer):
            daemon_threads = True
            def handle_error(self, *_args):
                pass
        self.server = Server(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server.server_port

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
