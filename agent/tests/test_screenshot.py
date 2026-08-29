import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap


class ScreenshotTests(unittest.TestCase):
    def test_capture_prefers_grim_and_uses_no_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "screen.png"
            calls = []

            def fake_runner(command, **kwargs):
                calls.append((command, kwargs))
                output.write_bytes(b"png")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            result = bootstrap.capture_screen(
                output,
                which=lambda name: "/usr/bin/grim" if name == "grim" else None,
                runner=fake_runner,
            )

            self.assertEqual(result, output)
            self.assertEqual(calls[0][0], ["/usr/bin/grim", str(output)])
            self.assertNotIn("shell", calls[0][1])

    def test_capture_falls_back_after_a_failed_utility(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "screen.png"
            attempted = []

            def fake_runner(command, **kwargs):
                attempted.append(Path(command[0]).name)
                if Path(command[0]).name == "grim":
                    return SimpleNamespace(returncode=1, stdout="", stderr="no display")
                output.write_bytes(b"png")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            bootstrap.capture_screen(
                output,
                which=lambda name: "/usr/bin/" + name if name in {"grim", "scrot"} else None,
                runner=fake_runner,
            )
            self.assertEqual(attempted, ["grim", "scrot"])

    def test_capture_reports_when_no_utility_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "No supported screenshot"):
                bootstrap.capture_screen(
                    Path(tmp) / "screen.png", which=lambda name: None
                )

    def test_capture_timeout_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "screen.png"

            def fake_runner(command, **kwargs):
                if Path(command[0]).name == "grim":
                    raise subprocess.TimeoutExpired(command, 30)
                output.write_bytes(b"png")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            self.assertEqual(
                bootstrap.capture_screen(
                    output,
                    which=lambda name: "/bin/" + name if name in {"grim", "import"} else None,
                    runner=fake_runner,
                ),
                output,
            )

    def test_uploaded_screenshot_is_deleted_after_send(self):
        observed = {}

        def fake_capture(path):
            Path(path).write_bytes(b"image")
            return Path(path)

        def fake_send(chat_id, path, caption=""):
            observed.update(chat_id=chat_id, path=Path(path), caption=caption)
            self.assertTrue(Path(path).exists())
            return "sent"

        with patch.object(bootstrap, "capture_screen", side_effect=fake_capture), \
             patch.object(bootstrap, "send_photo", side_effect=fake_send):
            self.assertEqual(bootstrap.capture_and_send_screenshot(42), "sent")

        self.assertEqual(observed["chat_id"], 42)
        self.assertEqual(observed["caption"], "Current screen")
        self.assertFalse(observed["path"].exists())

    def test_intro_advertises_direct_command(self):
        self.assertIn("/screenshot", bootstrap.INTRO)

    def test_uploading_action_uses_telegram_native_status(self):
        with patch.object(bootstrap, "tg", return_value=True) as telegram:
            self.assertTrue(bootstrap.send_uploading_photo(123))
        telegram.assert_called_once_with(
            "sendChatAction", request_timeout=10,
            chat_id=123, action="upload_photo",
        )


if __name__ == "__main__":
    unittest.main()
