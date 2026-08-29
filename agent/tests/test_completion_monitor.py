import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from completion_monitor import (
    CompletionMonitor, load_notification_enabled, save_notification_enabled,
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def codex_event(kind, payload):
    return json.dumps({"type": kind, "payload": payload}) + "\n"


class CompletionMonitorTests(unittest.TestCase):
    def make_monitor(self, root, enabled=True):
        state = root / "state.json"
        save_notification_enabled(state, enabled)
        claude = root / "claude"
        codex = root / "codex"
        return CompletionMonitor(
            state, lambda message: None,
            claude_sessions=claude, codex_sessions=codex,
        ), claude, codex

    def test_notification_choice_round_trips_and_invalid_state_is_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "choice.json"
            self.assertFalse(load_notification_enabled(path))
            save_notification_enabled(path, True)
            self.assertTrue(load_notification_enabled(path))
            save_notification_enabled(path, False)
            self.assertFalse(load_notification_enabled(path))
            path.write_text("broken", encoding="utf-8")
            self.assertFalse(load_notification_enabled(path))

    def test_claude_busy_to_idle_notifies_each_named_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"
            save_notification_enabled(state, True)
            claude = root / "claude"
            for pid, name, cwd in (("1", "api-review", "/work/api"),
                                   ("2", "web-fix", "/work/web")):
                write_json(claude / f"{pid}.json", {
                    "status": "busy", "name": name, "cwd": cwd,
                })
            monitor = CompletionMonitor(
                state, lambda message: None,
                claude_sessions=claude, codex_sessions=root / "codex",
            )
            write_json(claude / "1.json", {
                "status": "idle", "name": "api-review", "cwd": "/work/api",
            })
            write_json(claude / "2.json", {
                "status": "idle", "name": "web-fix", "cwd": "/work/web",
            })
            notices = monitor.scan_once()
            self.assertEqual(len(notices), 2)
            self.assertIn("Claude finished work in api (api-review).", notices)
            self.assertIn("Claude finished work in web (web-fix).", notices)
            self.assertEqual(monitor.scan_once(), [])

    def test_idle_claude_baseline_does_not_create_old_notification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor, claude, _ = self.make_monitor(root, enabled=False)
            write_json(claude / "1.json", {"status": "idle", "cwd": "/old"})
            monitor.set_enabled(True)
            self.assertEqual(monitor.scan_once(), [])

    def test_existing_codex_session_reports_only_appended_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"
            save_notification_enabled(state, True)
            codex = root / "codex"
            rollout = codex / "2026" / "rollout.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(codex_event("session_meta", {
                "session_id": "12345678-abcd", "cwd": "/work/project",
            }), encoding="utf-8")
            monitor = CompletionMonitor(
                state, lambda message: None,
                claude_sessions=root / "claude", codex_sessions=codex,
            )
            self.assertEqual(monitor.scan_once(), [])
            with rollout.open("a", encoding="utf-8") as stream:
                stream.write(codex_event("event_msg", {
                    "type": "task_complete", "error": None,
                }))
            self.assertEqual(
                monitor.scan_once(),
                ["Codex finished work in project (session 12345678)."],
            )
            self.assertEqual(monitor.scan_once(), [])

    def test_new_fast_codex_session_is_read_from_start_and_error_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor, _, codex = self.make_monitor(root)
            rollout = codex / "new.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(
                codex_event("session_meta", {
                    "session_id": "deadbeef-0000", "cwd": "/work/new",
                }) + codex_event("event_msg", {
                    "type": "task_complete", "error": {"message": "limit"},
                }),
                encoding="utf-8",
            )
            self.assertEqual(
                monitor.scan_once(),
                ["Codex finished work with an error in new (session deadbeef)."],
            )

    def test_incomplete_codex_line_waits_for_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor, _, codex = self.make_monitor(root)
            rollout = codex / "partial.jsonl"
            rollout.parent.mkdir(parents=True)
            line = codex_event("event_msg", {"type": "task_complete"})
            rollout.write_text(line[:-1], encoding="utf-8")
            self.assertEqual(monitor.scan_once(), [])
            with rollout.open("a", encoding="utf-8") as stream:
                stream.write("\n")
            self.assertEqual(monitor.scan_once(), ["Codex finished work."])

    def test_disabled_monitor_does_not_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor, claude, _ = self.make_monitor(root, enabled=False)
            write_json(claude / "1.json", {"status": "busy"})
            self.assertEqual(monitor.scan_once(), [])
            self.assertIn("off", monitor.status_text())


class HelpCommandTests(unittest.TestCase):
    def test_help_lists_completion_and_existing_commands(self):
        for command in (
            "/help", "/codex", "/claude", "/codex_claude_notify on",
            "/screenshot", "/provider", "/model", "/approve", "/deny",
        ):
            self.assertIn(command, bootstrap.HELP_TEXT)

    def test_intro_advertises_help_and_completion_notifications(self):
        self.assertIn("/help", bootstrap.INTRO)
        self.assertIn("/codex_claude_notify on", bootstrap.INTRO)

    def test_help_and_notification_commands_are_wired_before_model_chat(self):
        source = Path(bootstrap.__file__).read_text(encoding="utf-8")
        self.assertIn('if text == "/help"', source)
        self.assertIn('text.startswith("/codex_claude_notify ")', source)
        self.assertIn("completion_monitor.start()", source)

    def test_notification_state_is_persistent(self):
        self.assertEqual(
            bootstrap.CLI_NOTIFICATION_FILE,
            bootstrap.STATE / "memory" / "cli_notifications.json",
        )


if __name__ == "__main__":
    unittest.main()
