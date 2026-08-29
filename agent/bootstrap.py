#!/usr/bin/env python3

import json
import mimetypes
import os
import queue
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

from memory import DEFAULT_MESSAGE_LIMIT, RecentConversation
from providers import (
    PROVIDER_NAMES, ProviderConfigError, api_key_env_var, create_provider,
    image_input, load_selected_model, load_selected_provider,
    normalize_model_name, parse_provider_choice, save_selected_model,
    save_selected_provider,
)


ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
WORKSPACE = ROOT / "workspace"
AGENT_DIR = Path(__file__).resolve().parent
PROMPT = AGENT_DIR / "initial_prompt.md"
PROVIDER_FILE = STATE / "memory" / "provider.json"
MODEL_FILE = STATE / "memory" / "models.json"
CONVERSATION_FILE = STATE / "memory" / "recent_messages.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_OWNER_ID = os.environ.get("TELEGRAM_OWNER_ID", "")

# Keep ordinary requests quiet. If a model or command takes unusually long,
# reassure the owner occasionally without exposing chain-of-thought or logs.
PROGRESS_INITIAL_DELAY = 30
PROGRESS_INTERVAL = 60
TELEGRAM_ACTIVITY_INTERVAL = 4

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
I also remember the last 20 owner and assistant messages across restarts and version changes.
Send `/screenshot` whenever you want a current full-screen image.
Send `/codex your prompt` or `/claude your prompt` to stage text in an open CLI. I will show a screenshot and wait for your approval before submitting it.
Use `/model MODEL_ID` to change the model at runtime without creating a new version.
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
        conversation_file = Path(tmp) / "recent.json"
        test_memory = RecentConversation(conversation_file)
        for number in range(DEFAULT_MESSAGE_LIMIT + 1):
            test_memory.append("user", str(number), COMMIT_ID)
        memory_round_trip = (
            len(RecentConversation(conversation_file).messages)
            == DEFAULT_MESSAGE_LIMIT
        )

    provider_checks = [
        parse_provider_choice("openai") == "openai",
        parse_provider_choice(" Anthropic ") == "anthropic",
        parse_provider_choice("gemini") is None,
        parse_provider_choice("") is None,
        no_implicit_default,
        selection_round_trip,
        memory_round_trip,
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


def tg(method: str, request_timeout=70, **payload):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )

    r = requests.post(
        url,
        data=payload,
        timeout=request_timeout,
    )

    r.raise_for_status()

    data = r.json()

    if not data.get("ok"):
        raise RuntimeError(data)

    return data["result"]


def send(chat_id: int, text: str, **payload):
    for i in range(0, len(text) or 1, 3500):
        tg(
            "sendMessage",
            chat_id=chat_id,
            text=text[i:i + 3500] or " ",
            **payload,
        )


def send_uploading_photo(chat_id: int):
    """Show Telegram that a requested screenshot is being prepared."""
    return tg(
        "sendChatAction",
        request_timeout=10,
        chat_id=chat_id,
        action="upload_photo",
    )


def send_thinking(chat_id: int):
    """Post an honest, silent status while the model is processing.

    Telegram controls the labels for its fixed chat actions, so a bot cannot
    rename the native ``typing`` action to ``thinking``. Use a temporary
    message instead and return its ID so it can be removed afterward.
    """
    message = tg(
        "sendMessage",
        request_timeout=10,
        chat_id=chat_id,
        text="💭 Thinking…",
        disable_notification="true",
    )
    return message["message_id"]


def delete_message(chat_id: int, message_id: int):
    """Remove a temporary Telegram status message."""
    return tg(
        "deleteMessage",
        request_timeout=10,
        chat_id=chat_id,
        message_id=message_id,
    )


def run_with_thinking(operation, chat_id: int, notify):
    """Run model work while displaying a disposable Thinking status."""
    message_id = None
    try:
        try:
            message_id = send_thinking(chat_id)
        except Exception as exc:
            # Status feedback is cosmetic and must never block model work.
            print(f"Could not send thinking status: {exc}", file=sys.stderr)
        return run_with_progress(operation, notify)
    finally:
        if message_id is not None:
            try:
                delete_message(chat_id, message_id)
            except Exception as exc:
                print(f"Could not remove thinking status: {exc}", file=sys.stderr)


def run_with_progress(operation, notify, initial_delay=PROGRESS_INITIAL_DELAY,
                      interval=PROGRESS_INTERVAL, activity=None,
                      activity_interval=TELEGRAM_ACTIVITY_INTERVAL):
    """Run work with sparse messages and an optional transient heartbeat.

    Some operations provide a transient Telegram activity callback. Telegram
    chat actions expire after a few seconds, so that callback is refreshed while
    work remains unfinished. Callback failures are merely cosmetic and never
    replace the operation's result.
    """
    completed = queue.Queue(maxsize=1)

    def worker():
        try:
            completed.put((True, operation()))
        except BaseException as exc:
            completed.put((False, exc))

    threading.Thread(target=worker, daemon=True).start()
    started = time.monotonic()
    next_progress = started + initial_delay
    next_activity = started if activity is not None else float("inf")

    while True:
        now = time.monotonic()

        if now >= next_activity:
            try:
                activity()
            except Exception as exc:
                print(f"Could not send activity update: {exc}", file=sys.stderr)
            next_activity = time.monotonic() + activity_interval

        now = time.monotonic()
        if now >= next_progress:
            elapsed = max(1, int(now - started))
            try:
                notify(elapsed)
            except Exception as exc:
                # A failed courtesy update must not discard the real result.
                print(f"Could not send progress update: {exc}", file=sys.stderr)
            next_progress = time.monotonic() + interval

        timeout = max(0, min(next_progress, next_activity) - time.monotonic())
        try:
            succeeded, value = completed.get(timeout=timeout)
        except queue.Empty:
            continue

        if succeeded:
            return value
        raise value


def progress_text(elapsed_seconds: int) -> str:
    if elapsed_seconds < 60:
        return "Still working…"
    minutes = max(1, elapsed_seconds // 60)
    unit = "minute" if minutes == 1 else "minutes"
    return f"Still working… ({minutes} {unit})"


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


def capture_screen(path, which=shutil.which, runner=subprocess.run):
    """Capture the current full desktop into *path*.

    Prefer native Wayland capture, then try common desktop/X11 utilities. The
    command is run without a shell and the destination is fixed by Veyra.
    """
    destination = Path(path)
    candidates = [
        ("grim", ["grim", str(destination)]),
        ("gnome-screenshot", ["gnome-screenshot", "-f", str(destination)]),
        ("spectacle", ["spectacle", "-b", "-n", "-o", str(destination)]),
        ("scrot", ["scrot", str(destination)]),
        ("import", ["import", "-window", "root", str(destination)]),
    ]
    failures = []

    for executable, command in candidates:
        resolved = which(executable)
        if not resolved:
            continue
        command[0] = resolved
        try:
            result = runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="backslashreplace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{executable}: {exc}")
            continue

        if (result.returncode == 0 and destination.is_file()
                and destination.stat().st_size > 0):
            return destination

        detail = (result.stderr or result.stdout or
                  f"exit status {result.returncode}").strip()
        failures.append(f"{executable}: {detail}")
        destination.unlink(missing_ok=True)

    if not failures:
        raise RuntimeError(
            "No supported screenshot utility is installed "
            "(tried grim, gnome-screenshot, spectacle, scrot, and import)."
        )
    raise RuntimeError("Screen capture failed: " + "; ".join(failures))


def capture_and_send_screenshot(chat_id: int):
    """Capture a transient screenshot and upload it only to the owner chat."""
    with tempfile.TemporaryDirectory(prefix="veyra-screenshot-") as directory:
        image = capture_screen(Path(directory) / "screen.png")
        return send_photo(chat_id, image, caption="Current screen")


def screenshot_paths(command: str):
    """Return image paths explicitly mentioned by a screenshot command.

    Screenshot utilities commonly receive their output path as a quoted
    argument.  Parse shell quoting rather than splitting on whitespace, while
    deliberately limiting discovery to paths named by the approved command.
    """
    try:
        parts = shlex.split(command)
        paths = [
            Path(part) for part in parts[1:]
            if Path(part).suffix.lower() in {".png", ".jpg", ".jpeg"}
        ]
        return [path for path in paths if path.is_file()]
    except (OSError, ValueError):
        return []


def send_photo(chat_id: int, path, caption: str = ""):
    """Upload a locally-created screenshot to the owner's Telegram chat."""
    image = Path(path)
    with image.open("rb") as stream:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": (
                image.name,
                stream,
                mimetypes.guess_type(image.name)[0] or "application/octet-stream",
            )},
            timeout=70,
        )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data["result"]


class CodexError(RuntimeError):
    """A prompt could not be delivered to the open Codex terminal."""


def _process_info(path: Path):
    """Return (parent pid, has_tty, argv), tolerating vanishing /proc rows."""
    try:
        stat = (path / "stat").read_text(encoding="utf-8")
        # The command name is parenthesized and may itself contain spaces.
        fields = stat.rsplit(")", 1)[1].split()
        parent_pid = int(fields[1])
        has_tty = int(fields[4]) != 0
        argv = [part.decode("utf-8", "replace") for part in
                (path / "cmdline").read_bytes().split(b"\0") if part]
        return parent_pid, has_tty, argv
    except (OSError, ValueError, IndexError):
        return None


def _is_interactive_codex(argv: list[str], has_tty: bool) -> bool:
    if not has_tty or not argv:
        return False
    # npm's launcher is `node .../bin/codex`; the native launcher has codex as
    # argv[0]. Both remain attached to the terminal hosting the TUI.
    return any(Path(argument).name == "codex" for argument in argv[:2])


def find_open_codex_window(windows: list[dict], proc_root: Path = Path("/proc")) -> int:
    """Find the compositor window containing an interactive Codex process."""
    window_pids = {
        int(window["pid"]): window for window in windows
        if isinstance(window, dict) and window.get("pid") is not None
    }
    process_table = {}
    try:
        process_paths = list(proc_root.iterdir())
    except OSError as exc:
        raise CodexError(f"Could not inspect running applications: {exc}") from exc

    codex_pids = []
    for path in process_paths:
        if not path.name.isdigit():
            continue
        info = _process_info(path)
        if info is None:
            continue
        pid = int(path.name)
        process_table[pid] = info
        if _is_interactive_codex(info[2], info[1]):
            codex_pids.append(pid)

    matches = {}
    for pid in codex_pids:
        visited = set()
        while pid and pid not in visited:
            visited.add(pid)
            if pid in window_pids:
                matches[pid] = window_pids[pid]
                break
            info = process_table.get(pid)
            if info is None:
                info = _process_info(proc_root / str(pid))
                if info is None:
                    break
                process_table[pid] = info
            pid = info[0]

    if not matches:
        raise CodexError(
            "No open interactive Codex terminal was found. Open Codex in a "
            "terminal first, then try again."
        )
    if len(matches) == 1:
        return int(next(iter(matches.values()))["id"])

    focused = [window for window in matches.values() if window.get("is_focused")]
    if len(focused) == 1:
        return int(focused[0]["id"])
    raise CodexError(
        "More than one Codex terminal is open. Focus the intended one and try again."
    )


def stage_prompt_in_open_codex(prompt: str, which=shutil.which,
                              runner=subprocess.run,
                              proc_root: Path = Path("/proc")) -> int:
    """Focus the existing Codex terminal and type *prompt* without submitting."""
    prompt = prompt.strip()
    if not prompt:
        raise CodexError("A prompt is required. Use /codex followed by your prompt.")

    niri = which("niri")
    wtype = which("wtype")
    if not niri or not wtype:
        raise CodexError(
            "Desktop input support is unavailable (both niri and wtype are required)."
        )

    try:
        listed = runner(
            [niri, "msg", "--json", "windows"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexError(f"Could not inspect open windows: {exc}") from exc
    if listed.returncode != 0:
        detail = (listed.stderr or listed.stdout or "").strip()
        raise CodexError("Could not inspect open windows" +
                         (f": {detail}" if detail else "."))
    try:
        windows = json.loads(listed.stdout)
        if not isinstance(windows, list):
            raise ValueError("window list is not an array")
    except (json.JSONDecodeError, ValueError) as exc:
        raise CodexError("The desktop returned an invalid window list.") from exc

    window_id = find_open_codex_window(windows, proc_root)
    try:
        focused = runner(
            [niri, "msg", "action", "focus-window", "--id", str(window_id)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        if focused.returncode != 0:
            raise CodexError("Could not focus the open Codex terminal.")
        # stdin keeps arbitrary prompt text out of shell parsing and safely
        # handles text beginning with '-'. Pause for focus, type at a pace the
        # TUI can render (especially its slash-command menu), then let the final
        # frame settle before the caller captures the approval screenshot.
        typed = runner(
            [wtype, "-s", "150", "-d", "25", "-", "-s", "300"],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexError("Desktop input timed out while staging the prompt.") from exc
    except OSError as exc:
        raise CodexError(f"Could not stage desktop input: {exc}") from exc
    if typed.returncode != 0:
        detail = (typed.stderr or typed.stdout or "").strip()
        raise CodexError("Could not type into Codex" +
                         (f": {detail}" if detail else "."))
    return window_id


class ClaudeError(RuntimeError):
    """A prompt could not be delivered to the open Claude terminal."""


def _is_interactive_claude(argv: list[str], has_tty: bool) -> bool:
    """Recognize native and npm-installed interactive Claude Code CLIs."""
    if not has_tty or not argv:
        return False
    if Path(argv[0]).name in {"claude", "claude-code"}:
        return True
    # Older npm installs run `node .../@anthropic-ai/claude-code/cli.js`.
    return (
        Path(argv[0]).name in {"node", "nodejs"}
        and len(argv) > 1
        and "claude-code" in argv[1].lower()
    )


def find_open_claude_window(windows: list[dict],
                            proc_root: Path = Path("/proc")) -> int:
    """Find the compositor window containing interactive Claude Code."""
    window_pids = {
        int(window["pid"]): window for window in windows
        if isinstance(window, dict) and window.get("pid") is not None
    }
    process_table = {}
    try:
        process_paths = list(proc_root.iterdir())
    except OSError as exc:
        raise ClaudeError(f"Could not inspect running applications: {exc}") from exc

    claude_pids = []
    for path in process_paths:
        if not path.name.isdigit():
            continue
        info = _process_info(path)
        if info is None:
            continue
        pid = int(path.name)
        process_table[pid] = info
        if _is_interactive_claude(info[2], info[1]):
            claude_pids.append(pid)

    matches = {}
    for pid in claude_pids:
        visited = set()
        while pid and pid not in visited:
            visited.add(pid)
            if pid in window_pids:
                matches[pid] = window_pids[pid]
                break
            info = process_table.get(pid)
            if info is None:
                info = _process_info(proc_root / str(pid))
                if info is None:
                    break
                process_table[pid] = info
            pid = info[0]

    if not matches:
        raise ClaudeError(
            "No open interactive Claude terminal was found. Open Claude in a "
            "terminal first, then try again."
        )
    if len(matches) == 1:
        return int(next(iter(matches.values()))["id"])
    focused = [window for window in matches.values() if window.get("is_focused")]
    if len(focused) == 1:
        return int(focused[0]["id"])
    raise ClaudeError(
        "More than one Claude terminal is open. Focus the intended one and try again."
    )


def stage_prompt_in_open_claude(prompt: str, which=shutil.which,
                               runner=subprocess.run,
                               proc_root: Path = Path("/proc")) -> int:
    """Focus the existing Claude terminal and type *prompt* without submitting."""
    prompt = prompt.strip()
    if not prompt:
        raise ClaudeError("A prompt is required. Use /claude followed by your prompt.")

    niri = which("niri")
    wtype = which("wtype")
    if not niri or not wtype:
        raise ClaudeError(
            "Desktop input support is unavailable (both niri and wtype are required)."
        )
    try:
        listed = runner(
            [niri, "msg", "--json", "windows"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaudeError(f"Could not inspect open windows: {exc}") from exc
    if listed.returncode != 0:
        detail = (listed.stderr or listed.stdout or "").strip()
        raise ClaudeError("Could not inspect open windows" +
                          (f": {detail}" if detail else "."))
    try:
        windows = json.loads(listed.stdout)
        if not isinstance(windows, list):
            raise ValueError("window list is not an array")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ClaudeError("The desktop returned an invalid window list.") from exc

    window_id = find_open_claude_window(windows, proc_root)
    try:
        focused = runner(
            [niri, "msg", "action", "focus-window", "--id", str(window_id)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        if focused.returncode != 0:
            raise ClaudeError("Could not focus the open Claude terminal.")
        # Delayed keystrokes and a final pause keep slash commands visible in
        # the TUI before the approval screenshot is captured.
        typed = runner(
            [wtype, "-s", "150", "-d", "25", "-", "-s", "300"],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeError("Desktop input timed out while staging the prompt.") from exc
    except OSError as exc:
        raise ClaudeError(f"Could not stage desktop input: {exc}") from exc
    if typed.returncode != 0:
        detail = (typed.stderr or typed.stdout or "").strip()
        raise ClaudeError("Could not type into Claude" +
                          (f": {detail}" if detail else "."))
    return window_id


def act_on_staged_prompt(window_id: int, submit: bool, which=shutil.which,
                         runner=subprocess.run):
    """Submit or erase text staged in a specific terminal window."""
    niri = which("niri")
    wtype = which("wtype")
    if not niri or not wtype:
        raise RuntimeError(
            "Desktop input support is unavailable (both niri and wtype are required)."
        )
    try:
        focused = runner(
            [niri, "msg", "action", "focus-window", "--id", str(window_id)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        if focused.returncode != 0:
            raise RuntimeError("Could not focus the CLI terminal.")
        # Both Codex and Claude Code define Ctrl+C as cancelling a non-empty
        # editor input.  Ctrl+A followed by Backspace is not reliable here:
        # terminal editors commonly interpret Ctrl+A as “move to start”, which
        # leaves almost the entire staged prompt behind.  Since cancellation is
        # only offered while staged text is present, one Ctrl+C clears the input
        # without triggering the CLIs' empty-input/second-Ctrl+C exit behavior.
        action = ([wtype, "-k", "Return"] if submit else
                  [wtype, "-M", "ctrl", "-k", "c", "-m", "ctrl"])
        result = runner(
            action, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Could not submit the staged prompt." if submit
                else "Could not clear the staged prompt."
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not control the CLI terminal: {exc}") from exc


def approval_keyboard(token: str):
    """Telegram inline buttons for the one currently staged CLI prompt."""
    return json.dumps({"inline_keyboard": [[
        {"text": "✅ Send prompt", "callback_data": f"cli:approve:{token}"},
        {"text": "❌ Cancel", "callback_data": f"cli:cancel:{token}"},
    ]]})

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
            provider = create_provider(
                remembered, model=load_selected_model(MODEL_FILE, remembered)
            )
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

    # Conversation state lives outside agent/ so successor commits and
    # process restarts see the same bounded transcript.
    conversation = RecentConversation(
        CONVERSATION_FILE, limit=DEFAULT_MESSAGE_LIMIT
    )
    previous_response_id = None
    pending = None
    staged_cli = None
    offset = None
    active_instructions = instructions

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
            "switch. Use `/model MODEL_ID` to change the model without "
            "creating a new Veyra version. Choices are remembered across restarts.",
        ])

    def model_response(chat_id: int, **kwargs):
        return run_with_thinking(
            lambda: provider.create_response(**kwargs),
            chat_id,
            lambda elapsed: send(chat_id, progress_text(elapsed)),
        )

    def ask(chat_id: int, text: str, previous_id=None):
        nonlocal active_instructions
        if not previous_id:
            active_instructions = conversation.add_to_instructions(instructions)

        kwargs = {
            "instructions": active_instructions,
            "input": text,
            "tools": SHELL_TOOLS,
        }

        if previous_id:
            kwargs["previous_response_id"] = previous_id

        # Persist an accepted owner message before the remote request so a
        # crash or restart cannot make that message disappear.
        conversation.append("user", text, COMMIT_ID)
        return model_response(chat_id, **kwargs)

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
                "allowed_updates": '["message","callback_query"]',
            }

            if offset is not None:
                args["offset"] = offset

            updates = tg(
                "getUpdates",
                **args,
            )

            for update in updates:
                offset = update["update_id"] + 1

                callback = update.get("callback_query") or {}
                if callback:
                    sender = callback.get("from") or {}
                    message = callback.get("message") or {}
                    chat = message.get("chat") or {}
                    chat_id = chat.get("id")
                    data = callback.get("data", "")
                    try:
                        tg("answerCallbackQuery", request_timeout=10,
                           callback_query_id=callback.get("id"))
                    except Exception as exc:
                        print(f"Could not acknowledge approval button: {exc}",
                              file=sys.stderr)
                    if sender.get("id") != owner_id or chat_id is None:
                        continue
                    parts = data.split(":", 2)
                    if len(parts) != 3 or parts[:2] not in (["cli", "approve"],
                                                            ["cli", "cancel"]):
                        continue
                    if staged_cli is None or parts[2] != staged_cli["token"]:
                        send(chat_id, "That CLI approval is no longer current.")
                        continue
                    submit = parts[1] == "approve"
                    try:
                        act_on_staged_prompt(staged_cli["window_id"], submit)
                    except Exception as exc:
                        action = "send" if submit else "cancel"
                        send(chat_id, f"Could not {action} the staged prompt: {exc}")
                    else:
                        target = staged_cli["target"]
                        staged_cli = None
                        try:
                            tg("editMessageReplyMarkup", request_timeout=10,
                               chat_id=chat_id,
                               message_id=message.get("message_id"),
                               reply_markup=json.dumps({"inline_keyboard": []}))
                        except Exception as exc:
                            print(f"Could not remove approval buttons: {exc}",
                                  file=sys.stderr)
                        send(chat_id, (
                            f"Prompt sent to {target}." if submit
                            else f"Staged {target} prompt cancelled."
                        ))
                    continue

                msg = update.get("message") or {}
                sender = msg.get("from") or {}
                chat = msg.get("chat") or {}
                text = msg.get("text")
                if not text or sender.get("id") != owner_id:
                    continue
                chat_id = chat["id"]

                # ------------------------------------------------
                # First Telegram interaction
                # ------------------------------------------------

                if text == "/start":
                    send(chat_id, INTRO)
                    continue

                # ------------------------------------------------
                # Owner-requested full-screen capture
                # ------------------------------------------------

                if text == "/screenshot":
                    try:
                        run_with_progress(
                            lambda: capture_and_send_screenshot(chat_id),
                            lambda elapsed: send(
                                chat_id, progress_text(elapsed)
                            ),
                            activity=lambda: send_uploading_photo(chat_id),
                        )
                    except Exception as exc:
                        send(chat_id, f"Could not take screenshot: {exc}")
                    continue

                # ------------------------------------------------
                # Direct local Codex prompt
                # ------------------------------------------------

                if text == "/codex":
                    send(
                        chat_id,
                        "Use /codex followed by a prompt. I will stage it in "
                        "Codex, show you a screenshot, and wait for approval.",
                    )
                    continue

                if text.startswith("/codex "):
                    if staged_cli is not None:
                        send(chat_id, "Approve or cancel the currently staged prompt first.")
                        continue
                    prompt = text.split(maxsplit=1)[1]
                    try:
                        window_id = stage_prompt_in_open_codex(prompt)
                        staged_cli = {"target": "Codex", "window_id": window_id,
                                      "token": secrets.token_hex(8)}
                        capture_and_send_screenshot(chat_id)
                    except Exception as exc:
                        if staged_cli is not None:
                            try:
                                act_on_staged_prompt(window_id, False)
                                staged_cli = None
                            except Exception as clear_exc:
                                send(chat_id, f"Warning: could not clear staged text: {clear_exc}")
                        send(chat_id, f"Could not stage Codex prompt for approval: {exc}")
                    else:
                        send(chat_id, "Review the staged Codex prompt above, then approve or cancel.",
                             reply_markup=approval_keyboard(staged_cli["token"]))
                    continue

                # ------------------------------------------------
                # Direct local Claude prompt
                # ------------------------------------------------

                if text == "/claude":
                    send(
                        chat_id,
                        "Use /claude followed by a prompt. I will stage it in "
                        "Claude, show you a screenshot, and wait for approval.",
                    )
                    continue

                if text.startswith("/claude "):
                    if staged_cli is not None:
                        send(chat_id, "Approve or cancel the currently staged prompt first.")
                        continue
                    prompt = text.split(maxsplit=1)[1]
                    try:
                        window_id = stage_prompt_in_open_claude(prompt)
                        staged_cli = {"target": "Claude", "window_id": window_id,
                                      "token": secrets.token_hex(8)}
                        capture_and_send_screenshot(chat_id)
                    except Exception as exc:
                        if staged_cli is not None:
                            try:
                                act_on_staged_prompt(window_id, False)
                                staged_cli = None
                            except Exception as clear_exc:
                                send(chat_id, f"Warning: could not clear staged text: {clear_exc}")
                        send(chat_id, f"Could not stage Claude prompt for approval: {exc}")
                    else:
                        send(chat_id, "Review the staged Claude prompt above, then approve or cancel.",
                             reply_markup=approval_keyboard(staged_cli["token"]))
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
                        provider = create_provider(
                            choice, model=load_selected_model(MODEL_FILE, choice)
                        )
                    except ProviderConfigError as exc:
                        send(chat_id, str(exc))
                        continue

                    save_selected_provider(PROVIDER_FILE, choice)

                    # The old provider's conversation state cannot resume
                    # on the new one.
                    previous_response_id = None
                    active_instructions = instructions

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
                # Runtime model selection (no Veyra version change)
                # ------------------------------------------------

                if text == "/model":
                    send(
                        chat_id,
                        "No model provider is selected." if provider is None
                        else f"Current model: {provider.model} ({provider.name}).\n"
                             "Use /model MODEL_ID to change it.",
                    )
                    continue

                if text.startswith("/model "):
                    if provider is None:
                        send(chat_id, "Select a provider first with /provider openai or /provider anthropic.")
                        continue
                    model_name = normalize_model_name(text.split(maxsplit=1)[1])
                    if model_name is None:
                        send(chat_id, "Use /model MODEL_ID (the model ID must be non-empty and contain no spaces).")
                        continue
                    try:
                        provider = create_provider(provider.name, model=model_name)
                    except ProviderConfigError as exc:
                        send(chat_id, str(exc))
                        continue
                    save_selected_model(MODEL_FILE, provider.name, model_name)
                    previous_response_id = None
                    active_instructions = instructions
                    send(chat_id, f"Model set to {model_name} ({provider.name}). This does not create a Veyra version and is remembered across restarts.")
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
                            result = run_with_progress(
                                lambda command=command: run_local(command),
                                lambda elapsed: send(
                                    chat_id, progress_text(elapsed)
                                ),
                            )
                            results.append(result)

                            # Make screenshots produced by an approved shell
                            # command available to the vision-capable model,
                            # not merely as an opaque filesystem path.
                            for image_path in screenshot_paths(command):
                                # A screenshot is useful to the owner even
                                # when the model did not request vision
                                # analysis. Send it as a real Telegram photo,
                                # and also provide the same bytes to the
                                # selected model for follow-up analysis.
                                try:
                                    send_photo(
                                        chat_id,
                                        image_path,
                                        caption=f"Screenshot: {image_path.name}",
                                    )
                                except Exception as exc:
                                    send(
                                        chat_id,
                                        f"Could not send screenshot {image_path}: {exc}",
                                    )
                                try:
                                    outputs.append(image_input(image_path))
                                except OSError:
                                    pass

                        outputs.append({
                            "type": "shell_call_output",
                            "call_id": call.call_id,
                            "output": results,
                        })

                    pending = None

                    response = model_response(
                        chat_id,
                        instructions=active_instructions,
                        previous_response_id=response.id,
                        input=outputs,
                        tools=SHELL_TOOLS,
                    )

                else:
                    if provider is None:
                        send(chat_id, provider_status())
                        continue

                    response = ask(
                        chat_id,
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

                    # Commands are the one implementation detail retained
                    # in chat: owner approval must remain informed. Execution
                    # logs and model internals stay hidden.
                    lines = [
                        "Permission needed for local commands:",
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

                    reply_text = (
                        response.output_text or "(no text response)"
                    )
                    conversation.append(
                        "assistant", reply_text, COMMIT_ID
                    )
                    send(chat_id, reply_text)

        except KeyboardInterrupt:
            return 0

        except Exception as exc:
            print(
                f"Agent error: {exc}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
