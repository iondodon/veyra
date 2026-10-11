"""Local web chat, with authenticated access from other devices."""

import base64
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit

from api_credentials import CredentialError, CredentialStore, KEY_NAMES


EVENT_PREFIX = "VEYRA_EVENT "
WEB_PORT = 3000
WEB_URL = f"http://localhost:{WEB_PORT}"
DEFAULT_PASSWORD = "veyra"
ASSETS = Path(__file__).resolve().parent


class WebChat:
    def __init__(self, root):
        directory = Path(root) / "state/chat"
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.database = directory / "messages.sqlite3"
        self.credentials = CredentialStore(Path(root) / "state")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO settings(name, value) VALUES ('network_password', ?)",
                               (DEFAULT_PASSWORD,))
            self.network_password = connection.execute(
                "SELECT value FROM settings WHERE name = 'network_password'"
            ).fetchone()[0]
        self.database.chmod(0o600)
        self.lock = threading.RLock()
        self.process = None
        self.ready = False
        self.busy = False
        self.approval_id = None
        self.server = None
        self.begin_session()

    def begin_session(self):
        with closing(sqlite3.connect(self.database)) as connection:
            self.session_after = connection.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        self.session_id = secrets.token_hex(12)
        self.configuration = {"provider": "chatgpt", "model": None, "connected": False, "sign_in_url": None}

    def publish(self, text, *, role="system", kind="message", **fields):
        payload = {"role": role, "kind": kind, "text": self.credentials.redact(str(text)), **fields}
        with closing(sqlite3.connect(self.database, timeout=10)) as connection, connection:
            connection.execute("INSERT INTO events(payload) VALUES (?)", (json.dumps(payload, ensure_ascii=False),))

    def snapshot(self, after):
        with self.lock:
            with closing(sqlite3.connect(self.database, timeout=10)) as connection:
                rows = connection.execute(
                    "SELECT id, payload FROM events WHERE id > ? ORDER BY id LIMIT 200", (max(after, self.session_after),)
                ).fetchall()
            events = [{"id": row[0], **json.loads(row[1])} for row in rows]
            return {
                "events": [event for event in events if event.get("kind") != "status"],
                "cursor": rows[-1][0] if rows else max(after, self.session_after), "has_more": len(rows) == 200,
                "status": {"ready": self.ready, "busy": self.busy, "approval_id": self.approval_id,
                           "password_is_default": self.network_password == DEFAULT_PASSWORD,
                           "session_id": self.session_id, "configuration": dict(self.configuration)},
            }

    def key_settings(self, local):
        env = self.credentials.environment()
        return {"can_save_keys": local, "providers": [
            {"provider": name, "key_name": variable, "configured": bool(env.get(variable))}
            for name, variable in KEY_NAMES.items()
        ]}

    def save_api_key(self, payload):
        if not isinstance(payload, dict):
            return 400, {"error": "Choose a service and enter its API key."}
        with self.lock:
            if self.busy or self.approval_id:
                return 409, {"error": "Finish the current request before updating API keys."}
            try:
                self.credentials.save(payload.get("provider"), payload.get("key"))
            except CredentialError as exc:
                return 400, {"error": str(exc)}
            except OSError:
                return 500, {"error": "Veyra couldn't save the API key. Check the private state directory."}
            label = {"openai": "OpenAI", "anthropic": "Anthropic"}[payload["provider"]]
            self.publish(f"Your {label} API key is saved on this computer and will be available after a reboot. "
                         f"You can now ask me to use the {label} API.", role="assistant")
        return 200, {"ok": True}

    def change_password(self, payload):
        if not isinstance(payload, dict):
            return 400, {"error": "Enter your current and new passwords."}
        current = payload.get("current_password")
        new = payload.get("new_password")
        if not isinstance(current, str) or len(current) > 256:
            return 400, {"error": "Enter your current password."}
        if not isinstance(new, str) or not 8 <= len(new) <= 256:
            return 400, {"error": "Choose a new password of 8–256 characters."}
        with self.lock:
            if not secrets.compare_digest(current.encode(), self.network_password.encode()):
                return 403, {"error": "The current password is incorrect."}
            if new == current:
                return 400, {"error": "Choose a different password."}
            with closing(sqlite3.connect(self.database, timeout=10)) as connection, connection:
                connection.execute("UPDATE settings SET value = ? WHERE name = 'network_password'", (new,))
            self.network_password = new
        return 200, {"ok": True}

    def attach(self, process):
        with self.lock:
            self.begin_session()
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
                         kind=event.get("kind", "message"),
                         **({"provider": event.get("provider")} if event.get("kind") == "key_request" else {}))
        elif kind == "configuration":
            self.configuration.update({field: event[field] for field in self.configuration if field in event})
        elif kind == "sign_in":
            if event.get("provider", self.configuration["provider"]) == self.configuration["provider"]:
                self.configuration["sign_in_url"] = event.get("url")
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
        if self.credentials.contains_secret(text):
            return 400, {"error": "Use the private API key form to save keys securely.", "open_api_keys": True}
        with self.lock:
            if not self.ready or self.process is None:
                return 503, {"error": "Veyra is starting. Please wait a moment."}
            if self.busy:
                return 409, {"error": "Veyra is still working on your last message."}
            approval_id = payload.get("approval_id")
            approval = text.lower().rstrip(".!").strip()
            if approval in ("/approve", "/deny", "approve", "deny", "approve these commands", "deny these commands") and (not self.approval_id or approval_id != self.approval_id):
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
                port = self.server.server_port
                address = self.connection.getsockname()[0]
                return self.headers.get("Host") in (
                    f"localhost:{port}", f"127.0.0.1:{port}", f"{address}:{port}",
                )

            def local_client(self):
                return ipaddress.ip_address(self.client_address[0]).is_loopback

            def authenticated(self):
                if self.local_client():
                    return True
                scheme, _, credentials = self.headers.get("Authorization", "").partition(" ")
                expected = base64.b64encode(("veyra:" + chat.network_password).encode())
                return scheme.lower() == "basic" and secrets.compare_digest(credentials.encode(), expected)

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
                self.wfile.write(body)

            def do_GET(self):
                if not self.allowed_host():
                    return self.respond(403, {"error": "Open Veyra using this computer's IP address or localhost."})
                if not self.authenticated():
                    return self.respond(401, {"error": "Sign in with username veyra and your Veyra network password."})
                parsed = urlsplit(self.path)
                if parsed.path == "/api/events":
                    try:
                        after = max(0, int(parse_qs(parsed.query).get("after", ["0"])[0]))
                    except ValueError:
                        return self.respond(400, {"error": "Invalid message cursor."})
                    return self.respond(200, chat.snapshot(after))
                if parsed.path == "/api/settings":
                    try:
                        return self.respond(200, chat.key_settings(self.local_client()))
                    except (CredentialError, OSError):
                        return self.respond(500, {"error": "Veyra couldn't read the saved API key settings."})
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
                if not self.authenticated():
                    return self.respond(401, {"error": "Sign in with username veyra and your Veyra network password."})
                if self.path not in ("/api/messages", "/api/password", "/api/keys"):
                    return self.respond(404, {"error": "Page not found."})
                if self.path == "/api/keys" and not self.local_client():
                    return self.respond(403, {"error": "Open http://localhost:3000 on the computer running Veyra "
                                                       "to save API keys securely."})
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    return self.respond(415, {"error": "Send JSON."})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 100000:
                        return self.respond(413, {"error": "Message is too large."})
                    payload = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeDecodeError):
                    return self.respond(400, {"error": "Invalid message."})
                action = {"/api/password": chat.change_password, "/api/keys": chat.save_api_key,
                          "/api/messages": chat.submit}[self.path]
                status, result = action(payload)
                self.respond(status, result)

        class Server(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                # Reloads may close sockets mid-response. Never write to the terminal.
                pass

        self.server = Server(("0.0.0.0", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server.server_port

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
