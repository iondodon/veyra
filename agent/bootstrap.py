#!/usr/bin/env python3
"""Veyra's minimal agent. The supervisor carries its conversation to the web."""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from providers import (
    PROVIDER_NAMES, ProviderConfigError, create_provider, provider_available,
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
    "I'm Veyra. Tell me what you'd like me to do.\n\n"
    "I can inspect this project, work on this computer, and build my next version. "
    "I'll ask here before running commands.\n\n"
    "Choose /provider chatgpt to use your ChatGPT plan, or /provider openai "
    "or /provider anthropic for API billing. Use /connect to sign in with ChatGPT."
)


def emit(event_type, **fields):
    print(EVENT_PREFIX + json.dumps({"type": event_type, **fields}, ensure_ascii=False), flush=True)


def send(text, *, role="assistant", kind="message", **fields):
    emit("message", role=role, kind=kind, text=text, **fields)


def chat_error(exc):
    return str(exc).replace(LOGIN_HELP, "Use /connect to sign in with ChatGPT here.")


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

    def start(self):
        send(f"New agent session · commit {COMMIT_ID}. Earlier messages stay visible; model context starts fresh.",
             role="system", kind="status")
        send(INTRO)
        remembered = load_selected_provider(PROVIDER_FILE)
        if remembered:
            try:
                self.provider = create_provider(remembered)
                send(f"Using {self.provider.name} · {self.provider.model}.", role="system", kind="status")
            except ProviderConfigError as exc:
                send(chat_error(exc), role="system")
        else:
            send("Select a provider below to get started.", role="system")
        emit("ready")

    def provider_status(self):
        available = [name for name in PROVIDER_NAMES if provider_available(name)]
        return "\n".join([
            "No model provider is selected." if self.provider is None
            else f"Model provider: {self.provider.name} (model {self.provider.model}).",
            "Configured providers: " + (", ".join(available) or "none") + ".",
            "Select /provider chatgpt, /provider openai, or /provider anthropic.",
            "ChatGPT uses your plan allowance and authorized credits; OpenAI and Anthropic use API billing.",
            "Use /connect to sign in with ChatGPT, /account for account status, or /models for available models.",
        ])

    def select_provider(self, choice):
        provider = create_provider(choice)
        save_selected_provider(PROVIDER_FILE, choice)
        self.provider = provider
        self.previous_response_id = None
        self.pending = None
        emit("clear_approval")
        send(f"Using {choice} · {provider.model}. Your choice is saved.")

    def handle(self, text, approval_id=None):
        if text == "/start":
            send(INTRO)
            return
        if text == "/provider":
            send(self.provider_status())
            return
        if text.startswith("/provider "):
            choice = parse_provider_choice(text.split(maxsplit=1)[1])
            if choice is None:
                send("Choose /provider chatgpt, /provider openai, or /provider anthropic.")
            else:
                self.select_provider(choice)
            return
        if text == "/connect":
            auth = ChatGPTAuth(os.environ.get("VEYRA_CHATGPT_AUTH_FILE"))
            auth.login(open_browser=False, notify=send)
            # Connecting explicitly chooses ChatGPT plan usage, never API billing.
            self.select_provider("chatgpt")
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
                send(self.provider_status())
                return
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
            send(chat_error(exc), role="system")
        except Exception as exc:
            send(f"Veyra couldn't complete that request: {exc}", role="system")
        finally:
            emit("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
