import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap


class CodexBridgeTests(unittest.TestCase):
    def test_prompt_uses_stdin_and_workspace_sandbox_without_a_shell(self):
        observed = {}

        def fake_runner(command, **kwargs):
            observed.update(command=command, kwargs=kwargs)
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text("Codex answer\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="events", stderr="")

        answer = bootstrap.run_codex(
            "fix the tests; echo $HOME",
            which=lambda name: "/usr/bin/codex",
            runner=fake_runner,
        )

        self.assertEqual(answer, "Codex answer")
        self.assertEqual(observed["kwargs"]["input"], "fix the tests; echo $HOME")
        self.assertEqual(observed["kwargs"]["cwd"], bootstrap.WORKSPACE)
        self.assertNotIn("shell", observed["kwargs"])
        command = observed["command"]
        self.assertEqual(command[:2], ["/usr/bin/codex", "exec"])
        self.assertIn("workspace-write", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertEqual(command[-1], "-")

    def test_missing_cli_has_a_clear_error(self):
        with patch.dict(bootstrap.os.environ, {}, clear=True):
            with self.assertRaisesRegex(bootstrap.CodexError, "not installed"):
                bootstrap.run_codex("hello", which=lambda name: None)

    def test_empty_prompt_is_rejected_before_starting_cli(self):
        with self.assertRaisesRegex(bootstrap.CodexError, "prompt is required"):
            bootstrap.run_codex("  ")

    def test_timeout_has_a_clear_error(self):
        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with self.assertRaisesRegex(bootstrap.CodexError, "did not finish"):
            bootstrap.run_codex(
                "long task", timeout=60,
                which=lambda name: "/usr/bin/codex", runner=timeout,
            )

    def test_failed_cli_reports_bounded_diagnostic(self):
        def fail(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="bad auth")

        with self.assertRaisesRegex(bootstrap.CodexError, "bad auth"):
            bootstrap.run_codex(
                "hello", which=lambda name: "/usr/bin/codex", runner=fail,
            )

    def test_intro_advertises_codex_command(self):
        self.assertIn("/codex your prompt", bootstrap.INTRO)
        self.assertIn("workspace/", bootstrap.INTRO)


if __name__ == "__main__":
    unittest.main()
