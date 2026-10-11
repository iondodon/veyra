"""Local-only chat transport, kept alive while the agent changes commits."""

from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit


EVENT_PREFIX = "VEYRA_EVENT "
WEB_PORT = 3000
WEB_URL = f"http://localhost:{WEB_PORT}"
ASSETS = Path(__file__).resolve().parent


class WebChat:
    def __init__(self, root):
        directory = Path(root) / "state/chat"
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.database = directory / "messages.sqlite3"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)")
        self.database.chmod(0o600)
        self.lock = threading.RLock()
        self.process = None
        self.ready = False
        self.busy = False
        self.approval_id = None
        self.server = None

    def publish(self, text, *, role="system", kind="message", **fields):
        payload = {"role": role, "kind": kind, "text": str(text), **fields}
        with closing(sqlite3.connect(self.database, timeout=10)) as connection, connection:
            connection.execute("INSERT INTO events(payload) VALUES (?)", (json.dumps(payload, ensure_ascii=False),))

    def snapshot(self, after):
        with self.lock:
            with closing(sqlite3.connect(self.database, timeout=10)) as connection:
                rows = connection.execute(
                    "SELECT id, payload FROM events WHERE id > ? ORDER BY id LIMIT 200", (after,)
                ).fetchall()
            return {
                "events": [{"id": row[0], **json.loads(row[1])} for row in rows],
                "cursor": rows[-1][0] if rows else after, "has_more": len(rows) == 200,
                "status": {"ready": self.ready, "busy": self.busy, "approval_id": self.approval_id},
            }

    def attach(self, process):
        with self.lock:
            self.process = process
            self.ready = False
            self.busy = False
            self.approval_id = None
        threading.Thread(target=self.read_agent, args=(process,), daemon=True).start()

    def detach(self):
        with self.lock:
            if self.approval_id:
                self.publish("The pending command request was cancelled because Veyra restarted.")
            self.process = None
            self.ready = False
            self.busy = False
            self.approval_id = None

    def read_agent(self, process):
        try:
            for line in process.stdout:
                with self.lock:
                    if process is not self.process:
                        return
                    if line.startswith(EVENT_PREFIX):
                        try:
                            self.agent_event(json.loads(line[len(EVENT_PREFIX):]))
                        except (ValueError, TypeError, KeyError):
                            self.publish("Veyra sent an invalid chat event.")
                    elif line.strip():
                        self.publish(line.rstrip(), kind="status")
        except (OSError, ValueError, sqlite3.Error) as exc:
            try:
                self.publish(f"Veyra's chat connection stopped: {exc}")
            except sqlite3.Error:
                pass
        finally:
            process.stdout.close()
            with self.lock:
                if process is self.process:
                    self.detach()

    def agent_event(self, event):
        kind = event["type"]
        if kind == "message":
            role = event.get("role", "assistant")
            self.publish(event["text"], role=role if role in ("assistant", "tool", "system") else "assistant",
                         kind=event.get("kind", "message"))
        elif kind == "approval":
            self.approval_id = event["approval_id"]
            self.publish(event["text"], role="assistant", kind="approval", approval_id=self.approval_id)
        elif kind == "clear_approval":
            self.approval_id = None
        elif kind == "ready":
            self.ready = True
        elif kind == "done":
            self.busy = False

    def submit(self, payload):
        if not isinstance(payload, dict):
            return 400, {"error": "Send a chat message."}
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            return 400, {"error": "Enter a message of 1–20,000 characters."}
        text = text.strip()
        with self.lock:
            if not self.ready or self.process is None:
                return 503, {"error": "Veyra is starting. Please wait a moment."}
            if self.busy:
                return 409, {"error": "Veyra is still working on your last message."}
            approval_id = payload.get("approval_id")
            if text in ("/approve", "/deny") and (not self.approval_id or approval_id != self.approval_id):
                return 409, {"error": "That command request is no longer active."}
            self.busy = True
            self.publish(text, role="user")
            try:
                self.process.stdin.write(json.dumps({"text": text, "approval_id": approval_id}) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.detach()
                self.publish("Veyra disconnected before receiving your message. Please try again when it reconnects.")
                return 503, {"error": "Veyra is restarting."}
            return 202, {"ok": True}

    def start(self, port=WEB_PORT):
        chat = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def allowed_host(self):
                return self.headers.get("Host") in (f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}")

            def respond(self, status, data, content_type="application/json; charset=utf-8"):
                body = json.dumps(data).encode() if isinstance(data, dict) else data
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self.allowed_host():
                    return self.respond(403, {"error": "Open Veyra on localhost."})
                parsed = urlsplit(self.path)
                if parsed.path == "/api/events":
                    try:
                        after = max(0, int(parse_qs(parsed.query).get("after", ["0"])[0]))
                    except ValueError:
                        return self.respond(400, {"error": "Invalid message cursor."})
                    return self.respond(200, chat.snapshot(after))
                assets = {
                    "/": ("chat.html", "text/html; charset=utf-8"),
                    "/chat.css": ("chat.css", "text/css; charset=utf-8"),
                    "/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
                }
                if parsed.path not in assets:
                    return self.respond(404, {"error": "Page not found."})
                filename, content_type = assets[parsed.path]
                self.respond(200, (ASSETS / filename).read_bytes(), content_type)

            def do_POST(self):
                origin = self.headers.get("Origin")
                if not self.allowed_host() or origin != "http://" + self.headers.get("Host", ""):
                    return self.respond(403, {"error": "Send messages from the Veyra page."})
                if self.path != "/api/messages":
                    return self.respond(404, {"error": "Page not found."})
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    return self.respond(415, {"error": "Send JSON."})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 100000:
                        return self.respond(413, {"error": "Message is too large."})
                    payload = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeDecodeError):
                    return self.respond(400, {"error": "Invalid message."})
                status, result = chat.submit(payload)
                self.respond(status, result)

        class Server(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                # Reloads may close sockets mid-response. Never write to the terminal.
                pass

        self.server = Server(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server.server_port

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
