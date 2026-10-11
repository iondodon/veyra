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
    load_selected_provider, parse_provider_choice, save_selected_provider,
)
from chatgpt_auth import ChatGPTAuth, ChatGPTError, LOGIN_HELP


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
    "I'm Veyra. You can use me with your ChatGPT subscription or an API. "
    "Your ChatGPT subscription is the default; no API key is needed.\n\n"
    "If you prefer API billing, save the key for the service you choose:\n"
    "• OpenAI: OPENAI_API_KEY\n• Anthropic: ANTHROPIC_API_KEY\n\n"
    "Use the private API key form in this chat. Saved keys stay on this computer between reboots. "
    "I'll choose the model and tell you which one is in use. I'll ask before running commands."
)


def emit(event_type, **fields):
    with EMIT_LOCK:
        print(EVENT_PREFIX + json.dumps({"type": event_type, **fields}, ensure_ascii=False), flush=True)


def send(text, *, role="assistant", kind="message", **fields):
    emit("message", role=role, kind=kind, text=text, **fields)


def chat_error(exc):
    return (str(exc).replace(LOGIN_HELP, "Choose Continue with ChatGPT to sign in.")
            .replace("Select /provider chatgpt again.", "Ask me to use your ChatGPT subscription again.")
            .replace("Run agent/START --chatgpt-models.", "Try connecting your ChatGPT account again."))


def setup_intent(text):
    """Handle setup locally, including before any model has been connected."""
    value = re.sub(r"[.!?]+$", "", text.strip().lower()).strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^(?:please |i want to |i would like to |can you |could you )", "", value)
    if value in ("sign in", "sign in with chatgpt", "continue with chatgpt", "connect chatgpt",
                 "connect my chatgpt account", "sign me in"):
        return "connect", None
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
        if choice in ("subscription", "chatgpt", "chatgpt subscription", "chatgpt plan", "subscription mode"):
            return "provider", "chatgpt"
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
        self.login_url = None
        self.lock = threading.RLock()

    def start(self):
        send(INTRO, kind="setup")
        self.selected = load_selected_provider(PROVIDER_FILE) or "chatgpt"
        try:
            self.select_provider(self.selected)
        except ProviderConfigError as exc:
            self.configuration()
            if self.selected == "chatgpt" and not provider_available("chatgpt", os.environ):
                self.connect_subscription()
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
        emit("configuration", provider=self.selected or "chatgpt",
             model=self.provider.model if self.provider else None,
             connected=self.provider is not None, sign_in_url=self.login_url)

    def provider_status(self):
        if self.provider is None:
            return ("Sign in with your ChatGPT subscription to get started, or add an API key with the private form. "
                    "You can say ‘Use the OpenAI API’ or ‘Use the Anthropic API’ whenever you want to switch.")
        label = {"chatgpt": "your ChatGPT subscription", "openai": "the OpenAI API", "anthropic": "the Anthropic API"}
        return f"I'm using {label[self.provider.name]}. Model in use: {self.provider.model}."

    def select_provider(self, choice):
        env = dict(os.environ) if choice == "chatgpt" else self.environment()
        provider = create_provider(choice, env)
        save_selected_provider(PROVIDER_FILE, choice)
        self.selected = choice
        self.provider = provider
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
        send(text, kind="key_request", provider=provider)

    def sign_in_notice(self, message):
        if message.startswith("Continue with ChatGPT\n"):
            self.login_url = message.split("\n", 1)[1]
            emit("sign_in", url=self.login_url)

    def connect_subscription(self):
        # An explicit sign-in selects subscription usage, even before it finishes.
        save_selected_provider(PROVIDER_FILE, "chatgpt")
        self.selected = "chatgpt"
        self.provider = None
        self.previous_response_id = None
        self.pending = None
        emit("clear_approval")
        self.configuration()
        if self.login_thread and self.login_thread.is_alive():
            send("Continue with ChatGPT using the sign-in link in this chat.")
            return

        def finish():
            try:
                ChatGPTAuth(os.environ.get("VEYRA_CHATGPT_AUTH_FILE")).login(
                    open_browser=False, notify=self.sign_in_notice,
                )
                with self.lock:
                    self.login_url = None
                    if self.selected == "chatgpt":
                        self.select_provider("chatgpt")
                    else:
                        self.configuration()
                        send("Your ChatGPT subscription is connected. You can ask me to use it whenever you want.")
            except (ChatGPTError, ProviderConfigError, OSError) as exc:
                with self.lock:
                    self.login_url = None
                    self.configuration()
                    send(chat_error(exc), kind="error")
            finally:
                with self.lock:
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
        if action == "connect":
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
                send("You can use your ChatGPT subscription, the OpenAI API, or the Anthropic API.")
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
            send(chat_error(exc), kind="error")
        except Exception as exc:
            send(f"Veyra couldn't complete that request: {exc}", role="system")
        finally:
            emit("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
