import threading
import time
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap


class QuietChatTests(unittest.TestCase):
    def test_fast_work_produces_no_progress_message(self):
        notices = []
        result = bootstrap.run_with_progress(
            lambda: "answer", notices.append,
            initial_delay=0.1, interval=0.1,
        )
        self.assertEqual(result, "answer")
        self.assertEqual(notices, [])

    def test_long_work_produces_sparse_progress_messages(self):
        release = threading.Event()
        notices = []

        def operation():
            release.wait(timeout=1)
            return "answer"

        timer = threading.Timer(0.06, release.set)
        timer.start()
        try:
            result = bootstrap.run_with_progress(
                operation, notices.append,
                initial_delay=0.01, interval=0.02,
            )
        finally:
            timer.cancel()

        self.assertEqual(result, "answer")
        self.assertGreaterEqual(len(notices), 1)
        self.assertTrue(all(isinstance(elapsed, int) for elapsed in notices))

    def test_activity_is_immediate_and_refreshed_while_work_continues(self):
        release = threading.Event()
        activities = []

        def operation():
            release.wait(timeout=1)
            return "answer"

        timer = threading.Timer(0.07, release.set)
        timer.start()
        try:
            result = bootstrap.run_with_progress(
                operation, lambda elapsed: None,
                initial_delay=1,
                activity=lambda: activities.append(time.monotonic()),
                activity_interval=0.02,
            )
        finally:
            timer.cancel()

        self.assertEqual(result, "answer")
        self.assertGreaterEqual(len(activities), 2)

    def test_thinking_status_is_a_silent_message(self):
        calls = []

        def fake_tg(method, **payload):
            calls.append((method, payload))
            return {"message_id": 456}

        original = bootstrap.tg
        bootstrap.tg = fake_tg
        try:
            self.assertEqual(bootstrap.send_thinking(123), 456)
        finally:
            bootstrap.tg = original

        self.assertEqual(calls[0][0], "sendMessage")
        self.assertEqual(calls[0][1]["chat_id"], 123)
        self.assertEqual(calls[0][1]["text"], "💭 Thinking…")
        self.assertEqual(calls[0][1]["disable_notification"], "true")

    def test_thinking_status_is_removed_after_model_work(self):
        events = []
        original_send = bootstrap.send_thinking
        original_delete = bootstrap.delete_message
        bootstrap.send_thinking = lambda chat_id: events.append(
            ("send", chat_id)
        ) or 456
        bootstrap.delete_message = lambda chat_id, message_id: events.append(
            ("delete", chat_id, message_id)
        )
        try:
            result = bootstrap.run_with_thinking(
                lambda: events.append(("work",)) or "answer",
                123,
                lambda elapsed: None,
            )
        finally:
            bootstrap.send_thinking = original_send
            bootstrap.delete_message = original_delete

        self.assertEqual(result, "answer")
        self.assertEqual(
            events,
            [("send", 123), ("work",), ("delete", 123, 456)],
        )

    def test_thinking_status_is_removed_when_model_work_fails(self):
        deleted = []
        original_send = bootstrap.send_thinking
        original_delete = bootstrap.delete_message
        bootstrap.send_thinking = lambda chat_id: 456
        bootstrap.delete_message = lambda chat_id, message_id: deleted.append(
            (chat_id, message_id)
        )

        def fail():
            raise RuntimeError("provider failed")

        try:
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                bootstrap.run_with_thinking(
                    fail, 123, lambda elapsed: None
                )
        finally:
            bootstrap.send_thinking = original_send
            bootstrap.delete_message = original_delete

        self.assertEqual(deleted, [(123, 456)])

    def test_activity_failure_does_not_discard_result(self):
        def fail_activity():
            raise RuntimeError("telegram unavailable")

        result = bootstrap.run_with_progress(
            lambda: "answer", lambda elapsed: None,
            initial_delay=1, activity=fail_activity,
        )
        self.assertEqual(result, "answer")

    def test_operation_errors_are_propagated(self):
        def fail():
            raise RuntimeError("provider failed")

        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            bootstrap.run_with_progress(
                fail, lambda elapsed: None,
                initial_delay=0.1, interval=0.1,
            )

    def test_progress_copy_contains_no_model_internals(self):
        self.assertEqual(bootstrap.progress_text(30), "Still working…")
        self.assertEqual(
            bootstrap.progress_text(125), "Still working… (2 minutes)"
        )

    def test_shell_execution_logs_are_not_sent_to_chat(self):
        source = Path(bootstrap.__file__).read_text(encoding="utf-8")
        self.assertNotIn('preview[:3000]', source)
        self.assertNotIn('send(\n                                chat_id,\n                                f"$ {command}"', source)
        # Command disclosure is retained only for informed owner approval.
        self.assertIn("Permission needed for local commands:", source)


if __name__ == "__main__":
    unittest.main()
