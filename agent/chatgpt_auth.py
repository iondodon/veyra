"""Sign in with ChatGPT for this local Veyra installation.

Uses the documented public-client flow, never another application's tokens.
Credentials live outside agent/ and are never sent into the conversation.
"""

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid
import webbrowser

import requests


ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + DIRECT_SCOPE
DEFAULT_AUTH_FILE = Path(__file__).resolve().parents[1] / "state/auth/chatgpt.json"
LOGIN_HELP = "Run agent/START --chatgpt-login in a terminal."
TOKEN_FIELDS = ("access_token", "refresh_token", "id_token", "expires_at", "scopes")
TERMINAL_REFRESH_ERRORS = {
    "invalid_grant", "invalid_refresh_token", "token_expired",
    "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused",
}


class ChatGPTError(RuntimeError):
    """An actionable message safe to show without exposing credentials."""


def safe_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[\w.-]{1,100}", value) else "unknown"


class OAuthError(ChatGPTError):
    def __init__(self, status, code, request_id=None, body=None):
        self.status_code = status
        self.request_id = request_id
        self.body = body
        self.code = safe_code(code)
        super().__init__(f"ChatGPT authorization failed (HTTP {status}, {self.code}). {LOGIN_HELP}")


def http_json(method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=30, allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise ChatGPTError("Could not connect to ChatGPT. Try again later.") from None
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not 200 <= response.status_code < 300:
        error = data.get("error")
        code = error.get("code") if isinstance(error, dict) else error
        raise OAuthError(response.status_code, code, response.headers.get("x-request-id"), data)
    return data


def token_record(tokens, old=None):
    """Validate the rotating token set before replacing any saved credentials."""
    old = old or {}
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    expires = tokens.get("expires_in")
    if (not isinstance(access, str) or not access or
            not isinstance(refresh, str) or not refresh or
            not isinstance(expires, (int, float)) or expires <= 0 or
            not isinstance(tokens.get("token_type"), str) or
            tokens["token_type"].lower() != "bearer"):
        raise ChatGPTError("ChatGPT returned an incomplete token set. Sign in again.")
    scope = tokens.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ChatGPTError("ChatGPT returned invalid permissions.")
    return {
        "access_token": access, "refresh_token": refresh,
        "id_token": tokens.get("id_token") or old.get("id_token"),
        "expires_at": time.time() + expires,
        "scopes": scope.split() if scope is not None else old.get("scopes", []),
    }


def validate_identity(id_token, client_id, nonce):
    import jwt

    metadata = http_json("GET", ISSUER + "/.well-known/openid-configuration")
    jwks_uri = metadata.get("jwks_uri", "")
    if metadata.get("issuer") != ISSUER or not jwks_uri.startswith(ISSUER + "/"):
        raise ChatGPTError("ChatGPT returned invalid identity metadata.")
    try:
        keys = jwt.PyJWKClient(jwks_uri, timeout=30)
        key = keys.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token, key.key, algorithms=["RS256"], audience=client_id,
            issuer=ISSUER, options={"require": ["exp", "iss", "aud", "sub", "nonce"]},
        )
        if not secrets.compare_digest(str(claims["nonce"]), nonce) or not claims["sub"]:
            raise ValueError("identity mismatch")
    except (jwt.PyJWTError, ValueError, TypeError):
        raise ChatGPTError("ChatGPT identity verification failed. Sign in again.") from None
    return {"issuer": claims["iss"], "subject": claims["sub"], "email": claims.get("email", "")}


class ChatGPTAuth:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("VEYRA_CHATGPT_AUTH_FILE") or DEFAULT_AUTH_FILE)

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"profiles": {}}
        except (ValueError, OSError):
            raise ChatGPTError("Could not read ChatGPT credentials. Check the local credential file.") from None
        if (not isinstance(data, dict) or not isinstance(data.get("profiles"), dict) or
                any(not isinstance(profile, dict) or profile.get("client_id") != key
                    for key, profile in data["profiles"].items())):
            raise ChatGPTError("The local ChatGPT credential file has an invalid format.")
        return data

    def _write(self, data):
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".chatgpt-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def account(self):
        with self.locked():
            data = self._read()
            profile = data["profiles"].get(data.get("active_client_id"), {})
            if not profile.get("access_token"):
                raise ChatGPTError("ChatGPT is not signed in. " + LOGIN_HELP)
            if DIRECT_SCOPE not in profile.get("scopes", []):
                raise ChatGPTError("ChatGPT plan usage is disabled. " + LOGIN_HELP)
            return {k: profile.get(k) for k in ("client_id", "email", "issuer", "subject")}

    def access_token(self, client_id=None):
        # Read under an interprocess lock: START commands and the running agent
        # must not race a rotating refresh token or use a logged-out account.
        with self.locked():
            data = self._read()
            active = data.get("active_client_id")
            if client_id is not None and client_id != active:
                raise ChatGPTError("The ChatGPT account changed. Select /provider chatgpt again.")
            profile = data["profiles"].get(active, {})
            if not profile.get("access_token"):
                raise ChatGPTError("ChatGPT is not signed in. " + LOGIN_HELP)
            if DIRECT_SCOPE not in profile.get("scopes", []):
                raise ChatGPTError("ChatGPT plan usage is disabled. " + LOGIN_HELP)
            if profile.get("expires_at", 0) <= time.time() + 60:
                try:
                    tokens = http_json("POST", TOKEN_URL, data={
                        "grant_type": "refresh_token", "client_id": active,
                        "refresh_token": profile["refresh_token"], "resource": RESOURCE,
                    })
                except OAuthError as exc:
                    if exc.code in TERMINAL_REFRESH_ERRORS:
                        for field in TOKEN_FIELDS:
                            profile.pop(field, None)
                        self._write(data)
                    raise
                profile.update(token_record(tokens, profile))
                self._write(data)
                if DIRECT_SCOPE not in profile["scopes"]:
                    raise ChatGPTError("ChatGPT plan permission was removed. " + LOGIN_HELP)
            return profile["access_token"]

    def login(self, *, new_account=False, client_id=None, open_browser=True, timeout=300, notify=print):
        with self.locked():
            data = self._read()
            if "host_id" not in data:
                data["host_id"] = "urn:uuid:" + str(uuid.uuid4())
                self._write(data)
            selected = client_id or (None if new_account else
                                    data.get("active_client_id") or data.get("login_client_id"))
            if client_id and client_id not in data["profiles"]:
                raise ChatGPTError("Unknown ChatGPT account. See --chatgpt-status.")
            profile = dict(data["profiles"].get(selected, {}))
            host_id = data["host_id"]

        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        result = {}

        class Callback(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(5)
                super().setup()

            def log_message(self, *_args):
                pass  # The callback URL contains an authorization code.

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                returned_state = query.get("state", [""])[0]
                valid = (parsed.path == "/auth/callback" and returned_state.isascii() and
                         secrets.compare_digest(returned_state, state))
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"Return to Veyra to finish sign-in." if valid else b"Invalid sign-in callback.")
                if valid:
                    result.update({k: v[0] for k, v in query.items()})

        with ThreadingHTTPServer(("127.0.0.1", 0), Callback) as server:
            redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
            params = {
                "client_id": selected or "dynamic_agent_client", "ext_agent_host_id": host_id,
                "response_type": "code", "redirect_uri": redirect, "scope": SCOPES,
                "resource": RESOURCE, "state": state, "nonce": nonce,
                "code_challenge_method": "S256", "code_challenge": challenge,
            }
            if not selected:
                params["agent_name_hint"] = "Veyra"
            elif profile.get("access_token") and DIRECT_SCOPE not in profile.get("scopes", []):
                params["prompt"] = "consent"
            # Omit ID-token hints so the printable link never contains a token.
            url = AUTHORIZE_URL + "?" + urlencode(params)
            notify("Continue with ChatGPT\n" + url)
            if open_browser:
                webbrowser.open(url)
            server.timeout = 0.5
            deadline = time.monotonic() + timeout
            while not result and time.monotonic() < deadline:
                server.handle_request()
        if not result:
            raise ChatGPTError("ChatGPT sign-in timed out. " + LOGIN_HELP)
        if result.get("error"):
            raise ChatGPTError("ChatGPT sign-in was declined or failed. " + LOGIN_HELP)
        issued = result.get("client_id", selected)
        if not issued or issued == "dynamic_agent_client" or (selected and issued != selected):
            raise ChatGPTError("ChatGPT returned an invalid client registration.")
        if not result.get("code"):
            raise ChatGPTError("ChatGPT did not return an authorization code.")
        # Retain an issued registration even if its single-use code expires.
        with self.locked():
            data = self._read()
            data["profiles"].setdefault(issued, {"client_id": issued})
            data["login_client_id"] = issued
            self._write(data)
        tokens = http_json("POST", TOKEN_URL, data={
            "grant_type": "authorization_code", "client_id": issued,
            "code": result["code"], "code_verifier": verifier,
            "redirect_uri": redirect, "resource": RESOURCE,
        })
        identity = validate_identity(tokens.get("id_token"), issued, nonce)
        if profile.get("subject") and any(identity[k] != profile[k] for k in ("issuer", "subject")):
            raise ChatGPTError("A different ChatGPT account signed in. Use --new-account to add it.")
        saved = {"client_id": issued, **identity, **token_record(tokens)}
        with self.locked():
            data = self._read()
            data["profiles"][issued] = saved
            data["active_client_id"] = issued
            self._write(data)
        notify(f"Connected: {identity['email'] or identity['subject']}")
        notify("ChatGPT plan usage is " + ("enabled." if DIRECT_SCOPE in saved["scopes"] else
                                          "disabled. Sign in again to enable it."))

    def status(self):
        with self.locked():
            data = self._read()
            lines = []
            for key, profile in data["profiles"].items():
                state = "signed out"
                if profile.get("access_token"):
                    state = "plan usage enabled" if DIRECT_SCOPE in profile.get("scopes", []) else "plan usage disabled"
                marker = " (selected)" if key == data.get("active_client_id") else ""
                lines.append(f"{key}{marker}: {profile.get('email') or profile.get('subject') or 'registration pending'}; {state}")
            return "\n".join(lines) or "ChatGPT is not signed in. " + LOGIN_HELP

    def logout(self):
        with self.locked():
            data = self._read()
            active = data.get("active_client_id")
            profile = data["profiles"].get(active, {})
            confirmed = not profile.get("refresh_token")
            if not confirmed:
                try:
                    metadata = http_json("GET", ISSUER + "/.well-known/openid-configuration")
                    endpoint = metadata.get("revocation_endpoint", "")
                    if not endpoint.startswith(ISSUER + "/"):
                        raise ChatGPTError("Invalid revocation endpoint.")
                    for attempt in range(3):
                        try:
                            http_json("POST", endpoint, data={
                                "token": profile["refresh_token"], "token_type_hint": "refresh_token",
                                "client_id": active,
                            })
                            confirmed = True
                            break
                        except ChatGPTError:
                            if attempt == 2:
                                raise
                            time.sleep(0.5 * (2 ** attempt))
                except ChatGPTError:
                    pass
            for field in TOKEN_FIELDS:
                profile.pop(field, None)
            self._write(data)
            return "Signed out of ChatGPT." + ("" if confirmed else
                " Remote revocation was not confirmed. Disconnect Veyra in ChatGPT Settings → Usage.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Connect Veyra to your ChatGPT plan.")
    actions = parser.add_mutually_exclusive_group(required=True)
    for name in ("login", "status", "logout", "models"):
        actions.add_argument("--chatgpt-" + name, action="store_true")
    accounts = parser.add_mutually_exclusive_group()
    accounts.add_argument("--new-account", action="store_true")
    accounts.add_argument("--account", metavar="CLIENT_ID")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    auth = ChatGPTAuth()
    try:
        if args.chatgpt_login:
            auth.login(new_account=args.new_account, client_id=args.account, open_browser=not args.no_browser)
        elif args.chatgpt_logout:
            print(auth.logout())
        elif args.chatgpt_models:
            from chatgpt_provider import list_models
            for model in list_models(auth.access_token()):
                print(f"{model['slug']}\t{model.get('display_name', model['slug'])}")
        else:
            print(auth.status())
        return 0
    except (ChatGPTError, OSError) as exc:
        print(str(exc))
        return 1
