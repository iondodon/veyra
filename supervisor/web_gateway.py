"""One authenticated network listener, with an agent-independent recovery page."""

import base64
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import threading
from urllib.parse import urlsplit

from recovery import Recovery, RecoveryError

WEB_PORT = int(os.environ.get("VEYRA_WEB_PORT", "3000"))
WEB_URL = f"http://localhost:{WEB_PORT}"
# Transport-wide bound; application-specific validation belongs to the agent.
MAX_REQUEST_BYTES = 29 * 1024 * 1024
ASSETS = Path(__file__).resolve().parent


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=3):
        super().__init__("localhost", timeout=timeout)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class WebGateway:
    def __init__(self, root):
        self.root = Path(root)
        self.recovery = Recovery(root)
        self.endpoint = None
        self.active_revision = None
        self.server = None

    def publish(self, text, *, kind="status"):
        self.recovery.publish(text, kind=kind)

    def password(self):
        database = self.root / "state/chat/messages.sqlite3"
        if not database.exists():
            return "veyra"
        with closing(sqlite3.connect(database, timeout=3)) as connection:
            row = connection.execute("SELECT value FROM settings WHERE name = 'network_password'").fetchone()
        return row[0] if row else "veyra"

    def attach(self, process, revision):
        self.endpoint = (process.socket_path, process.web_token)
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
        self.endpoint = None
        self.active_revision = None

    def forward(self, method, path, body=None, headers=None):
        endpoint = self.endpoint
        if not endpoint:
            raise ConnectionError("Agent is offline")
        connection = UnixConnection(endpoint[0])
        try:
            connection.request(method, path, body=body,
                               headers={**(headers or {}), "X-Veyra-Token": endpoint[1]})
            response = connection.getresponse()
            return response.status, response.read(), response.getheader("Content-Type", "application/json")
        finally:
            connection.close()

    def ready(self):
        try:
            status, data, _ = self.forward("GET", "/_health")
            return status == 200 and json.loads(data).get("ready") is True
        except (OSError, ValueError, http.client.HTTPException):
            return False

    def start(self, port=WEB_PORT):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def local_client(self):
                local_hosts = (f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}")
                return (ipaddress.ip_address(self.client_address[0]).is_loopback
                        and self.headers.get("Host") in local_hosts)

            def allowed_host(self):
                hosts = self.headers.get_all("Host", [])
                if len(hosts) != 1:
                    return False
                host = hosts[0]
                port = self.server.server_port
                address = self.connection.getsockname()[0]
                if host in (f"localhost:{port}", f"127.0.0.1:{port}", f"{address}:{port}"):
                    return True
                # NAT may translate both the address and port. Keep external hosts
                # numeric so a hostname cannot be rebound through DNS to this server.
                public_address, separator, public_port = host.partition(":")
                if separator and (len(public_port) > 5 or not public_port.isascii() or not public_port.isdigit()
                                  or not 0 < int(public_port) <= 65535):
                    return False
                try:
                    public_ip = ipaddress.IPv4Address(public_address)
                except ValueError:
                    return False
                return public_ip.is_global and not public_ip.is_multicast

            def authenticated(self):
                if self.local_client():
                    return True
                scheme, _, value = self.headers.get("Authorization", "").partition(" ")
                expected = base64.b64encode(("veyra:" + gateway.password()).encode())
                return scheme.lower() == "basic" and secrets.compare_digest(value.encode(), expected)

            def respond(self, status, data, content_type="application/json; charset=utf-8"):
                body = json.dumps(data).encode() if isinstance(data, dict) else data
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                if status == 401:
                    self.send_header("WWW-Authenticate", 'Basic realm="Veyra", charset="UTF-8"')
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def authorize(self):
                if not self.allowed_host():
                    self.respond(403, {"error": "Open Veyra using localhost, this computer's IP address, or a public IPv4 address."})
                    return False
                try:
                    authenticated = self.authenticated()
                except sqlite3.Error:
                    self.respond(503, {"error": "Veyra couldn't read its network password."})
                    return False
                if not authenticated:
                    self.respond(401, {"error": "Sign in with username veyra and your Veyra network password."})
                    return False
                return True

            def proxy(self, body=None):
                try:
                    status, data, content_type = gateway.forward(self.command, self.path, body, {
                        "Host": self.headers["Host"], "Origin": self.headers.get("Origin", ""),
                        "Content-Type": self.headers.get("Content-Type", ""),
                        "X-Veyra-Local": "1" if self.local_client() else "0",
                    })
                    self.respond(status, data, content_type)
                except (OSError, http.client.HTTPException):
                    if self.command == "GET" and urlsplit(self.path).path == "/":
                        self.asset("recovery.html", "text/html; charset=utf-8")
                    else:
                        self.respond(503, {"error": "Veyra is unavailable. Open the recovery page.", "recovery": "/recovery"})

            def asset(self, name, content_type):
                self.respond(200, (ASSETS / name).read_bytes(), content_type)

            def do_GET(self):
                if not self.authorize():
                    return
                path = urlsplit(self.path).path
                if path == "/api/recovery":
                    return self.respond(200, gateway.recovery.status(gateway.ready(), gateway.active_revision))
                if path.startswith("/api/recovery/"):
                    return self.respond(404, {"error": "Recovery route not found."})
                assets = {"/recovery": ("recovery.html", "text/html; charset=utf-8"),
                          "/recovery.css": ("recovery.css", "text/css; charset=utf-8"),
                          "/recovery.js": ("recovery.js", "text/javascript; charset=utf-8")}
                if path in assets:
                    return self.asset(*assets[path])
                if path == "/" and not gateway.ready():
                    return self.asset("recovery.html", "text/html; charset=utf-8")
                return self.proxy()

            do_HEAD = do_GET

            def do_POST(self):
                if not self.authorize():
                    return
                if self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
                    return self.respond(403, {"error": "Send requests from the Veyra page."})
                path = urlsplit(self.path).path
                recovery_route = path == "/api/recovery" or path.startswith("/api/recovery/")
                if recovery_route and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.respond(415, {"error": "Send JSON."})
                if self.headers.get("Transfer-Encoding"):
                    return self.respond(400, {"error": "Use Content-Length."})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= (100000 if recovery_route else MAX_REQUEST_BYTES):
                        return self.respond(413, {"error": "Message is too large."})
                    body = self.rfile.read(length)
                    if len(body) != length:
                        return self.respond(400, {"error": "Incomplete message."})
                except (ValueError, OSError):
                    return self.respond(400, {"error": "Invalid message."})
                if recovery_route:
                    if self.command != "POST" or path not in ("/api/recovery/preview", "/api/recovery/action"):
                        return self.respond(404, {"error": "Recovery route not found."})
                    try:
                        payload = json.loads(body)
                        if not isinstance(payload, dict):
                            raise RecoveryError("Choose a recovery action.")
                        if path.endswith("/preview"):
                            return self.respond(200, gateway.recovery.preview(payload.get("action")))
                        gateway.recovery.request(payload)
                        return self.respond(202, {"ok": True})
                    except (ValueError, RecoveryError, OSError, subprocess.TimeoutExpired) as exc:
                        return self.respond(409, {"error": str(exc)})
                return self.proxy(body)

            do_PUT = do_POST
            do_PATCH = do_POST
            do_DELETE = do_POST
            do_OPTIONS = do_POST

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, *_args):
                pass

        self.server = Server(("0.0.0.0", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server.server_port

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
