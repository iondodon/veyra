#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from providers import (
    PROVIDER_NAMES, ProviderConfigError, api_key_env_var, create_provider,
    load_selected_provider, parse_provider_choice, save_selected_provider,
)


ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
WORKSPACE = ROOT / "workspace"
AGENT_DIR = Path(__file__).resolve().parent
PROMPT = AGENT_DIR / "initial_prompt.md"
PROVIDER_FILE = STATE / "memory" / "provider.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_OWNER_ID = os.environ.get("TELEGRAM_OWNER_ID", "")

SHELL_TOOLS = [
    {
        "type": "shell",
        "environment": {
            "type": "local",
        },
    }
]


def current_commit() -> str:
    """Identify the Git commit whose agent is checked out."""
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


COMMIT_ID = current_commit()


def load_instructions() -> str:
    return PROMPT.read_text(encoding="utf-8").replace(
        "{{COMMIT_ID}}", COMMIT_ID
    )


INTRO = f"""I'm online.

I'm running the agent from Git commit {COMMIT_ID}.

You shape what I become by talking to me. 
I can inspect my implementation, work on this computer, test improvements, and commit successor versions of myself. 
Describe the next version you want me to build.

The model provider is your explicit choice: select or switch it with `/provider openai` or `/provider anthropic`. 
The selection is remembered across restarts, and nothing runs on a provider you did not choose.
As a stating point only OpenAI and Anthropic providers are supported.

What should I become?"""


def self_test() -> int:
    required = [
        ROOT / "supervisor",
        ROOT / ".git",
        AGENT_DIR,
        STATE,
        WORKSPACE,
        PROMPT,
        Path(__file__).with_name("providers.py"),
    ]

    missing = [str(p) for p in required if not p.exists()]

    if missing:
        print(json.dumps({
            "ok": False,
            "missing": missing,
        }))
        return 1

    instructions = load_instructions()
    if "{{COMMIT_ID}}" in instructions or COMMIT_ID not in instructions:
        print(json.dumps({
            "ok": False,
            "error": "commit identity rendering failed",
        }))
        return 1

    # Provider selection must stay explicit and persistent — never inferred
    # from which API keys happen to be set.
    with tempfile.TemporaryDirectory() as tmp:
        selection_file = Path(tmp) / "provider.json"
        no_implicit_default = load_selected_provider(selection_file) is None
        save_selected_provider(selection_file, "anthropic")
        selection_round_trip = load_selected_provider(selection_file) == "anthropic"

    provider_checks = [
        parse_provider_choice("openai") == "openai",
        parse_provider_choice(" Anthropic ") == "anthropic",
        parse_provider_choice("gemini") is None,
        parse_provider_choice("") is None,
        no_implicit_default,
        selection_round_trip,
    ]

    if not all(provider_checks):
        print(json.dumps({
            "ok": False,
            "error": "provider invariant failed",
        }))
        return 1

    print(json.dumps({
        "ok": True,
        "commit": COMMIT_ID,
    }))

    return 0


def tg(method: str, **payload):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )

    r = requests.post(
        url,
        data=payload,
        timeout=70,
    )

    r.raise_for_status()

    data = r.json()

    if not data.get("ok"):
        raise RuntimeError(data)

    return data["result"]


def send(chat_id: int, text: str):
    for i in range(0, len(text) or 1, 3500):
        tg(
            "sendMessage",
            chat_id=chat_id,
            text=text[i:i + 3500] or " ",
        )


def send_intro(owner_id: int) -> bool:
    """
    Try to initiate the Telegram conversation.

    Telegram does not allow a bot to contact a user who has never
    interacted with that bot before. In that case the agent keeps running
    and waits for the owner to press Start.
    """

    try:
        send(owner_id, INTRO)
        return True

    except requests.HTTPError as exc:
        response = exc.response

        if response is not None and response.status_code in (400, 403):
            print(
                "\nVeyra is running, but Telegram has not allowed "
                "the bot to contact you yet.\n"
                "Open the bot in Telegram and press Start once.\n",
                file=sys.stderr,
            )
            return False

        raise

    except Exception as exc:
        print(
            f"Could not send startup message to Telegram: {exc}",
            file=sys.stderr,
        )
        return False


def run_local(command: str, timeout: int = 120):
    p = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        # Command output is external byte data and is not guaranteed to be
        # valid UTF-8. Preserve usable output instead of crashing the agent.
        encoding="utf-8",
        errors="backslashreplace",
        timeout=timeout,
    )

    return {
        "stdout": p.stdout,
        "stderr": p.stderr,
        "outcome": {
            "type": "exit",
            "exit_code": p.returncode,
        },
    }


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_OWNER_ID:
        print(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_ID",
            file=sys.stderr,
        )
        return 2

    try:
        owner_id = int(TELEGRAM_OWNER_ID)
    except ValueError:
        print(
            "TELEGRAM_OWNER_ID must be a numeric Telegram user ID",
            file=sys.stderr,
        )
        return 2

    instructions = load_instructions()

    # The model provider is an explicit, persisted owner decision. A
    # remembered selection is restored; without one the agent starts and
    # waits for /provider — no key in the environment implies a choice.
    provider = None
    provider_notice = None
    remembered = load_selected_provider(PROVIDER_FILE)

    if remembered:
        try:
            provider = create_provider(remembered)
        except ProviderConfigError as exc:
            provider_notice = (
                f"The remembered model provider '{remembered}' cannot "
                f"start: {exc}. Export the key and restart Veyra, or "
                "select another provider with /provider."
            )
    else:
        provider_notice = (
            "No model provider is selected yet. Choose one with "
            "`/provider openai` or `/provider anthropic`. The choice is "
            "remembered across restarts and can be switched at any time."
        )

    previous_response_id = None
    pending = None
    offset = None

    def provider_status() -> str:
        available = [
            name for name in PROVIDER_NAMES
            if os.environ.get(api_key_env_var(name))
        ]

        return "\n".join([
            "No model provider is selected." if provider is None
            else f"Model provider: {provider.name} (model {provider.model}).",
            "API keys available for: " + (", ".join(available) or "none") + ".",
            "Use `/provider openai` or `/provider anthropic` to select or "
            "switch. The choice is remembered across restarts.",
        ])

    def ask(text: str, previous_id=None):
        kwargs = {
            "instructions": instructions,
            "input": text,
            "tools": SHELL_TOOLS,
        }

        if previous_id:
            kwargs["previous_response_id"] = previous_id

        return provider.create_response(**kwargs)

    # --------------------------------------------------------
    # Veyra initiates the conversation whenever Telegram
    # permits it.
    # --------------------------------------------------------

    if send_intro(owner_id) and provider_notice:
        try:
            send(owner_id, provider_notice)
        except Exception as exc:
            print(
                f"Could not send provider notice: {exc}",
                file=sys.stderr,
            )

    # --------------------------------------------------------
    # Telegram loop
    # --------------------------------------------------------

    while True:
        try:
            args = {
                "timeout": 50,
                "allowed_updates": '["message"]',
            }

            if offset is not None:
                args["offset"] = offset

            updates = tg(
                "getUpdates",
                **args,
            )

            for update in updates:
                offset = update["update_id"] + 1

                msg = update.get("message") or {}
                sender = msg.get("from") or {}
                chat = msg.get("chat") or {}

                text = msg.get("text")

                if not text:
                    continue

                if sender.get("id") != owner_id:
                    continue

                chat_id = chat["id"]

                # ------------------------------------------------
                # First Telegram interaction
                # ------------------------------------------------

                if text == "/start":
                    send(chat_id, INTRO)
                    continue

                # ------------------------------------------------
                # Explicit model provider selection
                # ------------------------------------------------

                if text == "/provider":
                    send(chat_id, provider_status())
                    continue

                if text.startswith("/provider "):
                    choice = parse_provider_choice(
                        text.split(maxsplit=1)[1]
                    )

                    if choice is None:
                        send(
                            chat_id,
                            "Use /provider openai or /provider anthropic.",
                        )
                        continue

                    try:
                        provider = create_provider(choice)
                    except ProviderConfigError as exc:
                        send(chat_id, str(exc))
                        continue

                    save_selected_provider(PROVIDER_FILE, choice)

                    # The old provider's conversation state cannot resume
                    # on the new one.
                    previous_response_id = None

                    notice = (
                        f"Model provider set to {choice} "
                        f"(model {provider.model}). The choice is "
                        "remembered across restarts."
                    )

                    if pending:
                        pending = None
                        notice += " The pending shell request was cancelled."

                    send(chat_id, notice)
                    continue

                # ------------------------------------------------
                # Deny shell request
                # ------------------------------------------------

                if text == "/deny":
                    pending = None

                    send(
                        chat_id,
                        "Pending shell request denied.",
                    )

                    continue

                # ------------------------------------------------
                # Approve shell request
                # ------------------------------------------------

                if text == "/approve":
                    if not pending:
                        send(
                            chat_id,
                            "Nothing is waiting for approval.",
                        )
                        continue

                    response, calls = pending
                    outputs = []

                    for call in calls:
                        results = []

                        for command in call.action.commands:
                            send(
                                chat_id,
                                f"$ {command}",
                            )

                            result = run_local(command)
                            results.append(result)

                            preview = (
                                result["stdout"]
                                + "\n"
                                + result["stderr"]
                            ).strip()

                            if preview:
                                send(
                                    chat_id,
                                    preview[:3000],
                                )

                        outputs.append({
                            "type": "shell_call_output",
                            "call_id": call.call_id,
                            "output": results,
                        })

                    pending = None

                    response = provider.create_response(
                        instructions=instructions,
                        previous_response_id=response.id,
                        input=outputs,
                        tools=SHELL_TOOLS,
                    )

                else:
                    if provider is None:
                        send(chat_id, provider_status())
                        continue

                    response = ask(
                        text,
                        previous_response_id,
                    )

                # ------------------------------------------------
                # Handle model shell requests
                # ------------------------------------------------

                calls = [
                    x
                    for x in response.output
                    if getattr(x, "type", None)
                    == "shell_call"
                ]

                if calls:
                    pending = (
                        response,
                        calls,
                    )

                    lines = [
                        "Requested shell commands:",
                        "",
                    ]

                    for call in calls:
                        for command in call.action.commands:
                            lines.append(
                                f"$ {command}"
                            )

                    lines += [
                        "",
                        "Send /approve or /deny.",
                    ]

                    send(
                        chat_id,
                        "\n".join(lines),
                    )

                else:
                    previous_response_id = response.id

                    send(
                        chat_id,
                        response.output_text
                        or "(no text response)",
                    )

        except KeyboardInterrupt:
            return 0

        except Exception as exc:
            print(
                f"Agent error: {exc}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
