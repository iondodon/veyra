"""Completion notifications for locally running Claude Code and Codex sessions."""

import json
import os
import sys
import threading
from pathlib import Path


POLL_INTERVAL = 2.0


def load_notification_enabled(path: Path) -> bool:
    """Load the persisted opt-in, treating missing or invalid state as off."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("enabled") is True


def save_notification_enabled(path: Path, enabled: bool) -> None:
    """Atomically persist the notification opt-in outside versioned code."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps({"enabled": bool(enabled)}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _short_location(cwd) -> str:
    if not isinstance(cwd, str) or not cwd:
        return ""
    name = Path(cwd).name
    return f" in {name}" if name else ""


class CompletionMonitor:
    """Watch native CLI state and report each newly completed turn once.

    Claude Code publishes a small live session record whose status changes from
    ``busy`` to ``idle``. Codex appends a ``task_complete`` event to each JSONL
    session. These native signals avoid guessing from CPU usage or screenshots.
    """

    def __init__(self, state_file: Path, notifier, *, claude_sessions=None,
                 codex_sessions=None, interval=POLL_INTERVAL):
        home = Path.home()
        self.state_file = Path(state_file)
        self.notifier = notifier
        self.claude_sessions = Path(
            claude_sessions or home / ".claude" / "sessions"
        )
        self.codex_sessions = Path(
            codex_sessions or home / ".codex" / "sessions"
        )
        self.interval = interval
        self.enabled = load_notification_enabled(self.state_file)
        self._claude_status = {}
        self._codex_offsets = {}
        self._codex_details = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = None
        if self.enabled:
            self._baseline_locked()

    def status_text(self) -> str:
        state = "on" if self.enabled else "off"
        return f"Codex/Claude completion notifications are {state}."

    def set_enabled(self, enabled: bool) -> bool:
        """Set and persist monitoring; return whether the value changed."""
        enabled = bool(enabled)
        with self._lock:
            changed = self.enabled != enabled
            if enabled and changed:
                # Everything that predates this command is a baseline, not a
                # newly completed turn. Busy Claude sessions remain marked busy
                # so their later transition to idle is still reported.
                self._baseline_locked()
            self.enabled = enabled
            save_notification_enabled(self.state_file, enabled)
        self._wake.set()
        return changed

    def start(self):
        if self._thread is not None:
            return self._thread
        self._thread = threading.Thread(
            target=self._run, name="cli-completion-monitor", daemon=True
        )
        self._thread.start()
        return self._thread

    def _run(self):
        while True:
            self._wake.wait(self.interval)
            self._wake.clear()
            try:
                notices = self.scan_once()
                for notice in notices:
                    try:
                        self.notifier(notice)
                    except Exception as exc:
                        print(f"Could not send CLI completion notification: {exc}",
                              file=sys.stderr)
            except Exception as exc:
                # A transient malformed or disappearing session must not kill
                # monitoring for all other sessions.
                print(f"Could not inspect CLI completion state: {exc}",
                      file=sys.stderr)

    def _claude_files(self):
        try:
            return list(self.claude_sessions.glob("*.json"))
        except OSError:
            return []

    def _codex_files(self):
        try:
            return list(self.codex_sessions.rglob("*.jsonl"))
        except OSError:
            return []

    @staticmethod
    def _read_claude(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _read_codex_detail(path):
        try:
            with path.open("rb") as stream:
                event = json.loads(stream.readline().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if event.get("type") != "session_meta":
            return {}
        payload = event.get("payload") or {}
        return {"cwd": payload.get("cwd"), "id": payload.get("session_id")}

    def _baseline_locked(self):
        self._claude_status = {}
        for path in self._claude_files():
            data = self._read_claude(path)
            if data is not None:
                self._claude_status[path] = data.get("status")

        self._codex_offsets = {}
        self._codex_details = {}
        for path in self._codex_files():
            try:
                self._codex_offsets[path] = path.stat().st_size
                self._codex_details[path] = self._read_codex_detail(path)
            except OSError:
                pass

    def scan_once(self):
        """Return completion messages found since the previous scan."""
        with self._lock:
            if not self.enabled:
                return []
            notices = self._scan_claude_locked()
            notices.extend(self._scan_codex_locked())
            return notices

    def _scan_claude_locked(self):
        notices = []
        present = set()
        for path in self._claude_files():
            data = self._read_claude(path)
            if data is None:
                continue
            present.add(path)
            status = data.get("status")
            previous = self._claude_status.get(path)
            self._claude_status[path] = status
            if previous == "busy" and status == "idle":
                label = data.get("name")
                detail = f" ({label})" if isinstance(label, str) and label else ""
                notices.append(
                    f"Claude finished work{_short_location(data.get('cwd'))}{detail}."
                )
        self._claude_status = {
            path: status for path, status in self._claude_status.items()
            if path in present
        }
        return notices

    def _scan_codex_locked(self):
        notices = []
        present = set()
        for path in self._codex_files():
            present.add(path)
            if path not in self._codex_offsets:
                # A session created after monitoring began is new and should be
                # read from its beginning, even if a short turn finished before
                # the next two-second poll.
                self._codex_offsets[path] = 0
                self._codex_details[path] = {}
            offset = self._codex_offsets[path]
            try:
                size = path.stat().st_size
                if size < offset:
                    offset = 0
                with path.open("rb") as stream:
                    stream.seek(offset)
                    chunk = stream.read()
            except OSError:
                continue

            # Do not consume an incomplete JSONL record. It will be retried when
            # its terminating newline arrives.
            complete = chunk.rfind(b"\n") + 1
            if complete == 0:
                continue
            self._codex_offsets[path] = offset + complete
            detail = self._codex_details.get(path, {})
            for raw_line in chunk[:complete].splitlines():
                try:
                    event = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if event.get("type") == "session_meta":
                    payload = event.get("payload") or {}
                    detail = {
                        "cwd": payload.get("cwd"),
                        "id": payload.get("session_id") or payload.get("id"),
                    }
                    self._codex_details[path] = detail
                if event.get("type") != "event_msg":
                    continue
                payload = event.get("payload") or {}
                if payload.get("type") != "task_complete":
                    continue
                suffix = " with an error" if payload.get("error") else ""
                session_id = detail.get("id")
                session = (
                    f" (session {session_id[:8]})"
                    if isinstance(session_id, str) and session_id else ""
                )
                notices.append(
                    f"Codex finished work{suffix}"
                    f"{_short_location(detail.get('cwd'))}{session}."
                )
        self._codex_offsets = {
            path: offset for path, offset in self._codex_offsets.items()
            if path in present
        }
        self._codex_details = {
            path: detail for path, detail in self._codex_details.items()
            if path in present
        }
        return notices
