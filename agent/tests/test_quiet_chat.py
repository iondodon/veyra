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
