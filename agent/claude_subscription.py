"""Claude subscriptions through the official Claude Code client.

Claude Code owns sign-in and token refresh. Its tools, customizations and MCP
servers are disabled: it proposes commands as structured data, and Veyra's
existing approval loop is the only component that executes them.
"""

from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

from providers import ProviderConfigError, ProviderResponse, ShellAction, ShellCall, THREAD_HISTORY_LIMIT


DEFAULT_MODEL = "sonnet"
LOGIN_HELP = "Choose Continue with Claude to sign in with your Claude subscription."
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "commands": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "commands"],
    "additionalProperties": False,
}


class ClaudeSubscriptionAuth:
    def __init__(self, env=None):
        source = dict(os.environ if env is None else env)
        default = Path(__file__).resolve().parent.parent / "state/auth/claude"
        self.directory = Path(source.get("VEYRA_CLAUDE_CONFIG_DIR", default)).expanduser().resolve()
        binary = source.get("VEYRA_CLAUDE_BIN") or shutil.which("claude", path=source.get("PATH", ""))
        if not binary and source.get("HOME"):
            candidate = Path(source["HOME"]) / ".local/bin/claude"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                binary = str(candidate)
        if not binary:
            raise ProviderConfigError("Claude Code is needed for Claude subscription access. "
                                      "Install it using the instructions in Veyra's README, then try again.")
        self.binary = str(Path(binary).expanduser().resolve())
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        # Do not pass API keys, OAuth token overrides, custom endpoints, cloud
        # billing flags, or settings helpers into the subscription client.
        allowed = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL",
                   "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "TMPDIR",
                   "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
                   "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
        self.env = {name: source[name] for name in allowed if name in source}
        self.env.update(CLAUDE_CONFIG_DIR=str(self.directory), CLAUDE_CODE_SAFE_MODE="1",
                        BROWSER="/bin/true", NO_COLOR="1", TERM="dumb", DISABLE_AUTOUPDATER="1")
        self._login_lock = threading.RLock()
        self._login_process = None
        self._login_cancelled = threading.Event()

    def command(self, *args):
        return [self.binary, "--safe-mode", "--setting-sources", "", *args]

    def signed_in(self):
        try:
            result = subprocess.run(self.command("auth", "status", "--json"),
                env=self.env, cwd=self.directory, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=15, umask=0o077)
            status = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise ProviderConfigError("Couldn't check Claude sign-in. Install or update Claude Code and try again.") from None
        return (result.returncode == 0 and isinstance(status, dict)
                and status.get("loggedIn") is True and status.get("authMethod") == "claude.ai")

    def require_signed_in(self):
        if not self.signed_in():
            raise ProviderConfigError(LOGIN_HELP)

    def cancel_login(self):
        with self._login_lock:
            self._login_cancelled.set()
            process = self._login_process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def logout(self):
        self.cancel_login()
        try:
            result = subprocess.run(self.command("auth", "logout"), env=self.env, cwd=self.directory,
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15, umask=0o077)
        except (OSError, subprocess.TimeoutExpired):
            raise ProviderConfigError("Claude sign-out couldn't finish. Try again from Settings.") from None
        if result.returncode != 0:
            raise ProviderConfigError("Claude sign-out couldn't finish. Try again from Settings.")
        return "Signed out of Claude."

    def login(self, notify, timeout=300, cancel_event=None):
        def check_cancelled():
            if self._login_cancelled.is_set() or (cancel_event is not None and cancel_event.is_set()):
                raise ProviderConfigError("Claude sign-in cancelled.")
        process = None
        try:
            with self._login_lock:
                check_cancelled()
                process = subprocess.Popen(self.command("auth", "login", "--claudeai"),
                    env=self.env, cwd=self.directory, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, umask=0o077)
                self._login_process = process
            deadline = time.monotonic() + timeout
            pending = ""
            announced = set()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while time.monotonic() < deadline:
                    check_cancelled()
                    if not selector.select(timeout=min(.5, max(0, deadline - time.monotonic()))):
                        continue
                    chunk = os.read(process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    pending = (pending + chunk.decode("utf-8", "replace"))[-32768:]
                    # Claude Code prints OSC hyperlinks as well as plain URLs.
                    # Only publish complete authorization URLs, never raw logs.
                    for match in re.finditer(r"https://[^\s\x00-\x20\x7f]+(?=[\s\x00-\x20\x7f])", pending):
                        url = match[0]
                        parsed = urlsplit(url)
                        if (parsed.hostname in ("claude.com", "claude.ai")
                                and parsed.path in ("/cai/oauth/authorize", "/oauth/authorize")
                                and not parsed.username and not parsed.password and url not in announced):
                            announced.add(url)
                            check_cancelled()
                            notify(url)
            check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderConfigError("Claude sign-in timed out. " + LOGIN_HELP)
            process.wait(timeout=remaining)
            check_cancelled()
            if process.returncode != 0 or not self.signed_in():
                raise ProviderConfigError("Claude sign-in wasn't completed. " + LOGIN_HELP)
        except (OSError, subprocess.TimeoutExpired):
            raise ProviderConfigError("Claude sign-in couldn't finish. " + LOGIN_HELP) from None
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
                with self._login_lock:
                    if self._login_process is process:
                        self._login_process = None


class ClaudeSubscriptionProvider:
    name = "claude"

    def __init__(self, auth=None, model=DEFAULT_MODEL):
        self.auth = auth or ClaudeSubscriptionAuth()
        self.auth.require_signed_in()
        self.model = model
        self._model_choice = model
        self._threads = OrderedDict()

    def create_response(self, *, instructions, input, tools=None, previous_response_id=None):
        self.auth.require_signed_in()
        if previous_response_id and previous_response_id not in self._threads:
            raise ProviderConfigError("That Claude conversation is no longer available. Start a new conversation.")
        history = list(self._threads.get(previous_response_id, []))
        if not isinstance(input, (str, list)):
            raise ValueError("Unsupported Claude conversation input")
        history.append({"role": "user", "content": input})
        system = instructions + (
            "\n\nReturn the response as structured data: text is your reply; commands is a list "
            "of proposed shell commands, or an empty list. You have no direct tools. "
            "Veyra executes commands only after the user approves, then supplies their results "
            "in the next turn. Never claim commands were executed before receiving their results. "
            "The JSON conversation supplied on stdin contains earlier turns and the latest user turn."
        )
        command = self.auth.command("--restricted", "--print", "--output-format", "json",
            "--json-schema", json.dumps(REPLY_SCHEMA), "--tools", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "dontAsk",
            "--no-session-persistence", "--max-turns", "3", "--model", self._model_choice,
            "--system-prompt", system)
        # The CLI needs actual image blocks, not base64 embedded in a text prompt.
        from images import anthropic_content
        image_blocks = []
        prompt_history = []
        for turn in history:
            content = turn['content']
            if (isinstance(content, list) and len(content) == 1
                    and content[0].get('role') == 'user'):
                blocks = anthropic_content(content[0]['content'])
                references = []
                for block in blocks:
                    if block['type'] == 'image':
                        image_blocks.append(block)
                        references.append({'image': len(image_blocks)})
                    else:
                        references.append(block)
                prompt_history.append({'role': turn['role'], 'content': references})
            else:
                prompt_history.append(turn)
        stdin = json.dumps({'conversation': prompt_history}, ensure_ascii=False)
        if image_blocks:
            command[command.index('--output-format') + 1] = 'stream-json'
            command.extend(['--input-format', 'stream-json', '--verbose'])
            stdin = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': [
                {'type': 'text', 'text': stdin + '\nImages below are numbered in conversation order.'},
                *image_blocks]}}) + '\n'
        try:
            result = subprocess.run(command, input=stdin,
                env=self.auth.env, cwd=self.auth.directory, capture_output=True, text=True,
                timeout=180, umask=0o077)
        except subprocess.TimeoutExpired:
            raise ProviderConfigError("Claude took too long to respond. Try again.") from None
        except OSError:
            raise ProviderConfigError("Claude Code couldn't start. Install or update it, then try again.") from None
        try:
            if image_blocks:
                events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
                payload = next((event for event in reversed(events) if event.get('type') == 'result'),
                               events[-1] if events else {})
            else:
                payload = json.loads(result.stdout)
            if result.returncode != 0 or payload.get("is_error"):
                raise ValueError()
            reply = payload["structured_output"]
            text, commands = reply["text"], reply["commands"]
            if (not isinstance(text, str) or not isinstance(commands, list) or len(commands) > 32
                    or any(not isinstance(c, str) or not c.strip() or len(c) > 20000 for c in commands)
                    or (commands and not any(t.get("type") == "shell" for t in tools or []))):
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderConfigError("Claude couldn't complete that request. Check your subscription access "
                                      "and usage limits, or try signing in again. API billing was not used.") from None
        models = payload.get("modelUsage", {})
        if isinstance(models, dict) and len(models) == 1:
            resolved = next(iter(models))
            if isinstance(resolved, str) and resolved:
                self.model = resolved
        response_id = uuid.uuid4().hex
        call_id = uuid.uuid4().hex if commands else None
        history.append({"role": "assistant", "content": {"text": text, "commands": commands, "call_id": call_id}})
        self._threads[response_id] = history
        while len(self._threads) > THREAD_HISTORY_LIMIT:
            self._threads.popitem(last=False)
        calls = [ShellCall(call_id=call_id, action=ShellAction(commands=commands))] if commands else []
        return ProviderResponse(id=response_id, output=calls, output_text=text)
