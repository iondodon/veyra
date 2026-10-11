#!/usr/bin/env python3
"""Veyra's minimal agent. The supervisor carries its conversation to the web."""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "supervisor"))
from api_credentials import CredentialError, CredentialStore, KEY_NAMES
from providers import (
    PROVIDER_NAMES, ProviderConfigError, api_key_env_var, create_provider, provider_available,
    clear_selected_provider, load_selected_provider, parse_provider_choice, save_selected_provider,
)
from chatgpt_auth import ChatGPTAuth, ChatGPTError, LOGIN_HELP
from claude_subscription import ClaudeSubscriptionAuth, LOGIN_HELP as CLAUDE_LOGIN_HELP


ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
WORKSPACE = ROOT / "workspace"
AGENT_DIR = Path(__file__).resolve().parent
PROMPT = AGENT_DIR / "initial_prompt.md"
PROVIDER_FILE = STATE / "memory" / "provider.json"
EVENT_PREFIX = "VEYRA_EVENT "
SHELL_TOOLS = [{"type": "shell", "environment": {"type": "local"}}]
EMIT_LOCK = threading.Lock()


def current_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


COMMIT_ID = current_commit()


def load_instructions() -> str:
    return PROMPT.read_text(encoding="utf-8").replace("{{COMMIT_ID}}", COMMIT_ID)


INTRO = (
    "I'm Veyra. Choose how you'd like to connect:\n\n"
    "• Sign in with your ChatGPT subscription.\n"
    "• Sign in with your Claude subscription.\n"
    "• Use an API: save OPENAI_API_KEY for OpenAI or ANTHROPIC_API_KEY for Anthropic "
    "in the private API key form, then choose that API.\n\n"
    "I'll choose the model for your selected service and tell you its name. "
    "Your sign-ins, API keys, and service choice are saved for future starts. "
    "You can also choose a service or add keys in Settings. "
    "I'll ask before running commands."
)

SETUP_HELP = (
    "Choose how you'd like to connect in Settings: ChatGPT subscription, Claude subscription, "
    "OpenAI API, or Anthropic API. For API access, add your key under Settings → API keys. "
    "You can also use the sign-in and private API key buttons below."
)


def emit(event_type, **fields):
    with EMIT_LOCK:
        print(EVENT_PREFIX + json.dumps({"type": event_type, **fields}, ensure_ascii=False), flush=True)


def send(text, *, role="assistant", kind="message", **fields):
    emit("message", role=role, kind=kind, text=text, **fields)


def chat_error(exc):
    return (str(exc).replace(LOGIN_HELP, SETUP_HELP).replace(CLAUDE_LOGIN_HELP, SETUP_HELP)
            .replace("Select /provider chatgpt again.", "Ask me to use your ChatGPT subscription again.")
            .replace("Run agent/START --chatgpt-models.", "Try connecting your ChatGPT account again."))


def setup_intent(text):
    """Handle setup locally, including before any model has been connected."""
    value = re.sub(r"[.!?]+$", "", text.strip().lower()).strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^(?:please |i want to |i would like to |can you |could you )", "", value)
    if value in ("log out", "logout", "log me out", "sign out", "sign me out", "disconnect", "disconnect current service"):
        return "logout", None
    logout = re.fullmatch(r"(?:log out|logout|log me out|sign out|sign me out) (?:of|from) "
                          r"(?:my |the )?(chatgpt|claude|anthropic)(?: account| subscription)?", value)
    if logout:
        return "logout", "chatgpt" if logout[1] == "chatgpt" else "claude"
    if value in ("sign in", "sign me in"):
        return "subscriptions", None
    if value in ("sign in with chatgpt", "continue with chatgpt", "connect chatgpt",
                 "connect my chatgpt account"):
        return "connect", None
    if value in ("sign in with claude", "sign in with anthropic", "continue with claude", "connect claude",
                 "connect my claude account", "connect my anthropic account"):
        return "connect", "claude"
    if value in ("what model are you using", "which model are you using", "what model is in use",
                 "show model", "show settings", "what mode are you using"):
        return "status", None
    if (any(word in value.split() for word in ("add", "set", "save", "change", "update", "manage", "configure"))
            and ("api key" in value or "openai_api_key" in value or "anthropic_api_key" in value)):
        provider = "anthropic" if "anthropic" in value else "openai" if "openai" in value else None
        return "keys", provider
    match = re.fullmatch(r"(?:use|switch to|connect to) (?:my |the |an? )?(.+?)(?: please)?", value)
    if match:
        choice = match[1]
        if choice in ("subscription", "subscription mode"):
            return "subscriptions", None
        if choice in ("chatgpt", "chatgpt subscription", "chatgpt plan"):
            return "provider", "chatgpt"
        if choice in ("claude", "claude subscription", "claude plan", "anthropic subscription", "anthropic plan"):
            return "provider", "claude"
        if choice in ("openai", "openai api", "anthropic", "anthropic api"):
            return "provider", choice.split()[0]
        if choice in ("api", "api mode"):
            return "keys", None
    return None, None


def run_local(command: str, timeout: int = 120):
    try:
        p = subprocess.run(
            command, shell=True, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="backslashreplace", timeout=timeout,
        )
        return {
            "stdout": p.stdout, "stderr": p.stderr,
            "outcome": {"type": "exit", "exit_code": p.returncode},
        }
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode("utf-8", "backslashreplace") if isinstance(value, bytes) else value or ""
        return {
            "stdout": decode(exc.stdout), "stderr": decode(exc.stderr),
            "outcome": {"type": "timeout", "seconds": timeout},
        }


class Conversation:
    def __init__(self):
        self.instructions = load_instructions()
        self.provider = None
        self.previous_response_id = None
        self.pending = None
        self.credentials = CredentialStore(STATE)
        self.selected = None
        self.active_api_key = None
        self.login_thread = None
        self.login_cancel = None
        self.login_url = None
        self.claude_login_thread = None
        self.claude_login_cancel = None
        self.claude_login_auth = None
        self.claude_login_url = None
        self.reported_model = None
        self.lock = threading.RLock()

    def start(self):
        send(INTRO, kind="setup")
        self.selected = load_selected_provider(PROVIDER_FILE)
        if self.selected is None:
            self.configuration()
            emit("ready")
            return
        try:
            self.select_provider(self.selected)
        except ProviderConfigError as exc:
            self.configuration()
            if self.selected == "chatgpt" and not provider_available("chatgpt", os.environ):
                self.connect_subscription()
            elif self.selected == "claude":
                try:
                    self.connect_claude_subscription()
                except ProviderConfigError as error:
                    send(chat_error(error), kind="error")
            else:
                send(chat_error(exc), kind="error")
                if self.selected in KEY_NAMES:
                    self.request_key(self.selected)
        emit("ready")

    def environment(self):
        try:
            return self.credentials.environment()
        except (CredentialError, OSError) as exc:
            raise ProviderConfigError(str(exc)) from None

    def configuration(self):
        emit("configuration", provider=self.selected,
             model=self.provider.model if self.provider else None,
             connected=self.provider is not None,
             sign_in_url=self.claude_login_url if self.selected == "claude" else
             self.login_url if self.selected == "chatgpt" else None)

    def provider_status(self):
        if self.provider is None:
            return SETUP_HELP
        label = {"chatgpt": "your ChatGPT subscription", "claude": "your Claude subscription",
                 "openai": "the OpenAI API", "anthropic": "the Anthropic API"}
        return f"I'm using {label[self.provider.name]}. Model in use: {self.provider.model}."

    def clear_connection(self):
        self.selected = None
        self.provider = None
        self.active_api_key = None
        self.reported_model = None
        self.previous_response_id = None
        self.pending = None
        self.login_url = None
        self.claude_login_url = None
        emit("clear_approval")
        self.configuration()
        clear_selected_provider(PROVIDER_FILE)

    def logout(self, choice=None):
        current = self.selected or (self.provider.name if self.provider else None)
        disconnect = choice is None or choice == current
        target = choice or current
        for service, cancelled in (("chatgpt", self.login_cancel), ("claude", self.claude_login_cancel)):
            if cancelled and (choice is None or choice == service):
                cancelled.set()
        if self.claude_login_auth and (choice is None or choice == "claude"):
            self.claude_login_auth.cancel_login()
        if disconnect:
            self.clear_connection()
        try:
            if target == "chatgpt":
                message = ChatGPTAuth(os.environ.get("VEYRA_CHATGPT_AUTH_FILE")).logout()
            elif target == "claude":
                auth = self.claude_login_auth or ClaudeSubscriptionAuth(self.subscription_environment())
                message = auth.logout()
            else:
                message = "Veyra is disconnected."
        except (ChatGPTError, ProviderConfigError, OSError) as exc:
            send(chat_error(exc), kind="error")
            message = "Sign-out could not be fully confirmed."
        send(message + "\n\n" + (SETUP_HELP if disconnect else self.provider_status()),
             kind="setup" if disconnect else "message")

    def report_error(self, exc):
        with self.lock:
            auth_help = LOGIN_HELP in str(exc) or CLAUDE_LOGIN_HELP in str(exc)
            if auth_help and self.provider and self.provider.name in ("chatgpt", "claude"):
                try:
                    available = provider_available(self.provider.name, self.subscription_environment())
                except (ProviderConfigError, OSError):
                    available = False
                if not available:
                    name = "ChatGPT" if self.provider.name == "chatgpt" else "Claude"
                    self.clear_connection()
                    send(name + " is disconnected.\n\n" + SETUP_HELP, kind="setup")
                    return
            send(chat_error(exc), kind="error")

    def select_provider(self, choice):
        env = self.subscription_environment() if choice in ("chatgpt", "claude") else self.environment()
        provider = create_provider(choice, env)
        save_selected_provider(PROVIDER_FILE, choice)
        self.selected = choice
        self.provider = provider
        self.reported_model = provider.model
        self.active_api_key = env.get(api_key_env_var(choice))
        self.previous_response_id = None
        self.pending = None
        emit("clear_approval")
        self.configuration()
        send(self.provider_status())

    def request_key(self, provider=None):
        label = {"openai": "OpenAI", "anthropic": "Anthropic"}.get(provider)
        text = (f"Add your {label} API key with the private form below, then ask me to use the {label} API."
                if label else "Choose OpenAI or Anthropic in the private API key form below. "
                "OpenAI uses OPENAI_API_KEY; Anthropic uses ANTHROPIC_API_KEY.")
        send(text + " You can also open this form from Settings → API keys.", kind="key_request", provider=provider)

    def sign_in_notice(self, message):
        if message.startswith("Continue with ChatGPT\n"):
            self.login_url = message.split("\n", 1)[1]
            if self.selected == "chatgpt":
                emit("sign_in", provider="chatgpt", url=self.login_url)

    def subscription_environment(self):
        env = dict(os.environ)
        env.setdefault("VEYRA_CLAUDE_CONFIG_DIR", str(STATE / "auth/claude"))
        return env

    def connect_claude_subscription(self):
        auth = ClaudeSubscriptionAuth(self.subscription_environment())
        save_selected_provider(PROVIDER_FILE, "claude")
        self.selected = "claude"
        self.provider = None
        self.previous_response_id = None
        self.pending = None
        emit("clear_approval")
        self.configuration()
        if (self.claude_login_thread and self.claude_login_thread.is_alive()
                and not self.claude_login_cancel.is_set()):
            send("Continue with Claude using the sign-in link in this chat.")
            return
        send("Sign in with your Claude subscription using Continue with Claude. "
             "Complete sign-in on the computer running Veyra; your login will be saved for later starts.")
        cancelled = threading.Event()
        self.claude_login_cancel = cancelled
        self.claude_login_auth = auth

        def sign_in_notice(url):
            with self.lock:
                if cancelled.is_set():
                    return
                self.claude_login_url = url
                if self.selected == "claude":
                    emit("sign_in", provider="claude", url=url)

        def finish():
            try:
                auth.login(notify=sign_in_notice, cancel_event=cancelled)
                with self.lock:
                    if cancelled.is_set():
                        return
                    self.claude_login_url = None
                    if self.selected == "claude":
                        self.select_provider("claude")
                    else:
                        self.configuration()
                        send("Your Claude subscription is connected. You can ask me to use it whenever you want.")
            except (ProviderConfigError, OSError) as exc:
                with self.lock:
                    if cancelled.is_set():
                        return
                    self.claude_login_url = None
                    self.configuration()
                    send(chat_error(exc), kind="error")
            finally:
                with self.lock:
                    if self.claude_login_cancel is cancelled:
                        self.claude_login_thread = None

        self.claude_login_thread = threading.Thread(target=finish, daemon=True)
        self.claude_login_thread.start()

    def connect_subscription(self):
        # An explicit sign-in selects subscription usage, even before it finishes.
        save_selected_provider(PROVIDER_FILE, "chatgpt")
        self.selected = "chatgpt"
        self.provider = None
        self.previous_response_id = None
        self.pending = None
        emit("clear_approval")
        self.configuration()
        if self.login_thread and self.login_thread.is_alive() and not self.login_cancel.is_set():
            send("Continue with ChatGPT using the sign-in link in this chat.")
            return

        cancelled = threading.Event()
        self.login_cancel = cancelled

        def sign_in_notice(message):
            with self.lock:
                if not cancelled.is_set():
                    self.sign_in_notice(message)

        def finish():
            try:
                ChatGPTAuth(os.environ.get("VEYRA_CHATGPT_AUTH_FILE")).login(
                    open_browser=False, notify=sign_in_notice, cancel_event=cancelled,
                )
                with self.lock:
                    if cancelled.is_set():
                        return
                    self.login_url = None
                    if self.selected == "chatgpt":
                        self.select_provider("chatgpt")
                    else:
                        self.configuration()
                        send("Your ChatGPT subscription is connected. You can ask me to use it whenever you want.")
            except (ChatGPTError, ProviderConfigError, OSError) as exc:
                with self.lock:
                    if cancelled.is_set():
                        return
                    self.login_url = None
                    self.configuration()
                    send(chat_error(exc), kind="error")
            finally:
                with self.lock:
                    if self.login_cancel is cancelled:
                        self.login_thread = None

        self.login_thread = threading.Thread(target=finish, daemon=True)
        self.login_thread.start()

    def handle(self, text, approval_id=None):
        with self.lock:
            return self.handle_locked(text, approval_id)

    def handle_locked(self, text, approval_id=None):
        if self.credentials.contains_secret(text):
            self.request_key()
            return
        approval = re.sub(r"[.!]+$", "", text.strip().lower())
        if approval in ("approve", "approve these commands", "deny", "deny these commands"):
            text = "/approve" if approval.startswith("approve") else "/deny"
        action, choice = setup_intent(text)
        if action == "logout":
            self.logout(choice)
            return
        if action == "subscriptions":
            send("Choose Continue with ChatGPT or Continue with Claude below, or choose a subscription in Settings.", kind="setup")
            return
        if action == "connect":
            if choice == "claude":
                self.connect_claude_subscription()
            else:
                self.connect_subscription()
            return
        if action == "keys":
            self.request_key(choice)
            return
        if action == "status":
            send(self.provider_status())
            return
        if action == "provider":
            if choice == "chatgpt" and not provider_available("chatgpt", os.environ):
                self.connect_subscription()
            elif choice == "claude" and not provider_available("claude", self.subscription_environment()):
                self.connect_claude_subscription()
            elif choice in KEY_NAMES and not self.environment().get(api_key_env_var(choice)):
                self.request_key(choice)
            else:
                self.select_provider(choice)
            return
        if text == "/start":
            send(INTRO, kind="setup")
            return
        if text == "/provider":
            send(self.provider_status())
            return
        if text.startswith("/provider "):
            choice = parse_provider_choice(text.split(maxsplit=1)[1])
            if choice is None:
                send("You can use your ChatGPT subscription, your Claude subscription, the OpenAI API, or the Anthropic API.")
            else:
                self.select_provider(choice)
            return
        if text == "/connect":
            self.connect_subscription()
            return
        if text == "/account":
            send(chat_error(ChatGPTAuth(os.environ.get("VEYRA_CHATGPT_AUTH_FILE")).status()))
            return
        if text == "/models":
            from chatgpt_provider import list_models
            auth = ChatGPTAuth(os.environ.get("VEYRA_CHATGPT_AUTH_FILE"))
            models = list_models(auth.access_token())
            send("Available ChatGPT models:\n" + "\n".join(m["slug"] for m in models))
            return
        if text in ("/approve", "/deny"):
            if not self.pending or approval_id != self.pending[2]:
                send("That approval is no longer active. Review the current command request.")
                return
            response, calls, _ = self.pending
            # Consume before execution: repeated approvals never execute twice.
            self.pending = None
            emit("clear_approval")
            if text == "/deny":
                self.previous_response_id = None
                send("Command request denied. No commands were run.")
                return
            outputs = []
            for call in calls:
                results = []
                for command in call.action.commands:
                    send("$ " + command, role="tool", kind="command")
                    result = run_local(command)
                    for stream in ("stdout", "stderr"):
                        result[stream] = self.credentials.redact(result[stream])
                    results.append(result)
                    preview = (result["stdout"] + "\n" + result["stderr"]).strip()
                    outcome = result["outcome"]
                    summary = (f"Exit code {outcome['exit_code']}." if outcome["type"] == "exit"
                               else f"Timed out after {outcome['seconds']} seconds.")
                    send((preview[:64000] + ("\n[Output truncated]" if len(preview) > 64000 else "")
                          + "\n" + summary).strip(), role="tool", kind="output")
                outputs.append({"type": "shell_call_output", "call_id": call.call_id, "output": results})
            response = self.provider.create_response(
                instructions=self.instructions, previous_response_id=response.id,
                input=outputs, tools=SHELL_TOOLS,
            )
        else:
            if self.pending:
                send("Approve or deny the pending commands first, or select another provider to cancel them.")
                return
            if self.provider is None:
                send(self.provider_status(), kind="setup")
                return
            if self.provider.name in KEY_NAMES:
                current_key = self.environment().get(api_key_env_var(self.provider.name))
                if current_key != self.active_api_key:
                    self.select_provider(self.provider.name)
            kwargs = {"instructions": self.instructions, "input": text, "tools": SHELL_TOOLS}
            if self.previous_response_id:
                kwargs["previous_response_id"] = self.previous_response_id
            response = self.provider.create_response(**kwargs)
        self.show_response(response)

    def show_response(self, response):
        if self.provider.name == "claude" and self.provider.model != self.reported_model:
            self.reported_model = self.provider.model
            self.configuration()
            send(self.provider_status())
        calls = [item for item in response.output if getattr(item, "type", None) == "shell_call"]
        if response.output_text:
            send(response.output_text)
        if calls:
            approval_id = uuid.uuid4().hex
            self.pending = (response, calls, approval_id)
            commands = [command for call in calls for command in call.action.commands]
            emit("approval", approval_id=approval_id,
                 text="Veyra wants to run these commands:\n\n" + "\n".join("$ " + c for c in commands))
        else:
            self.previous_response_id = response.id
            if not response.output_text:
                send("(No text response.)")


def self_test() -> int:
    required = [
        ROOT / "supervisor", ROOT / ".git", AGENT_DIR, STATE, WORKSPACE, PROMPT,
        AGENT_DIR / "providers.py", AGENT_DIR / "chatgpt_auth.py", AGENT_DIR / "chatgpt_provider.py",
        AGENT_DIR / "claude_subscription.py",
        ROOT / "supervisor/api_credentials.py",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        print(json.dumps({"ok": False, "missing": missing}))
        return 1
    instructions = load_instructions()
    if "{{COMMIT_ID}}" in instructions or COMMIT_ID not in instructions:
        print(json.dumps({"ok": False, "error": "commit identity rendering failed"}))
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        selection_file = Path(tmp) / "provider.json"
        no_default = load_selected_provider(selection_file) is None
        save_selected_provider(selection_file, "anthropic")
        round_trip = load_selected_provider(selection_file) == "anthropic"
    if not (no_default and round_trip and all(parse_provider_choice(p) == p for p in PROVIDER_NAMES)):
        print(json.dumps({"ok": False, "error": "provider invariant failed"}))
        return 1
    print(json.dumps({"ok": True, "commit": COMMIT_ID}))
    return 0


def main() -> int:
    if any(arg.startswith("--chatgpt-") for arg in sys.argv[1:]):
        from chatgpt_auth import main as auth_main
        return auth_main(sys.argv[1:])
    if "--self-test" in sys.argv:
        return self_test()
    if os.environ.get("VEYRA_WEB_TRANSPORT") != "1":
        # Direct START also uses the same quiet web startup.
        os.execv(sys.executable, [sys.executable, str(ROOT / "supervisor/supervisor")])
    conversation = Conversation()
    conversation.start()
    for line in sys.stdin:
        try:
            message = json.loads(line)
            conversation.handle(message["text"], message.get("approval_id"))
        except (ChatGPTError, ProviderConfigError) as exc:
            conversation.report_error(exc)
        except Exception as exc:
            send(f"Veyra couldn't complete that request: {exc}", role="system")
        finally:
            emit("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
