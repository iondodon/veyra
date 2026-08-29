import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap


def add_process(proc, pid, ppid, argv, tty=34816):
    path = proc / str(pid)
    path.mkdir()
    # After the parenthesized comm: state, ppid, pgrp, session, tty_nr.
    (path / "stat").write_text(
        f"{pid} (process) S {ppid} {pid} {pid} {tty} {pid} 0 0\n",
        encoding="utf-8",
    )
    (path / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


class CodexBridgeTests(unittest.TestCase):
    def test_prompt_is_typed_into_existing_codex_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            add_process(proc, 100, 1, ["/usr/bin/ghostty"], tty=0)
            add_process(proc, 200, 100, ["node", "/home/me/bin/codex", "resume"])
            observed = []

            def runner(command, **kwargs):
                observed.append((command, kwargs))
                if command[-2:] == ["--json", "windows"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps([{"id": 7, "pid": 100, "is_focused": True}]),
                        stderr="",
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            window = bootstrap.send_prompt_to_open_codex(
                "fix the tests; echo $HOME",
                which=lambda name: "/usr/bin/" + name,
                runner=runner,
                proc_root=proc,
            )

        self.assertEqual(window, 7)
        self.assertEqual(observed[1][0][-3:], ["focus-window", "--id", "7"])
        self.assertEqual(observed[2][0][0], "/usr/bin/wtype")
        self.assertEqual(observed[2][1]["input"], "fix the tests; echo $HOME")
        self.assertEqual(observed[2][0][-2:], ["-k", "Return"])
        self.assertFalse(any("exec" in command for command, kwargs in observed))

    def test_focused_window_wins_when_multiple_codex_sessions_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            for terminal, codex in ((100, 200), (101, 201)):
                add_process(proc, terminal, 1, ["ghostty"], tty=0)
                add_process(proc, codex, terminal, ["codex", "resume"])
            windows = [
                {"id": 7, "pid": 100, "is_focused": False},
                {"id": 8, "pid": 101, "is_focused": True},
            ]
            self.assertEqual(bootstrap.find_open_codex_window(windows, proc), 8)

    def test_missing_open_session_has_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(bootstrap.CodexError, "No open interactive"):
                bootstrap.find_open_codex_window([], Path(tmp))

    def test_empty_prompt_is_rejected_before_desktop_inspection(self):
        with self.assertRaisesRegex(bootstrap.CodexError, "prompt is required"):
            bootstrap.send_prompt_to_open_codex("  ")

    def test_missing_desktop_tools_has_clear_error(self):
        with self.assertRaisesRegex(bootstrap.CodexError, "niri and wtype"):
            bootstrap.send_prompt_to_open_codex("hello", which=lambda name: None)

    def test_bad_window_listing_is_reported(self):
        result = SimpleNamespace(returncode=0, stdout="not json", stderr="")
        with self.assertRaisesRegex(bootstrap.CodexError, "invalid window list"):
            bootstrap.send_prompt_to_open_codex(
                "hello", which=lambda name: "/bin/" + name,
                runner=lambda *args, **kwargs: result,
            )

    def test_intro_describes_existing_interactive_session(self):
        self.assertIn("already open", bootstrap.INTRO)
        self.assertNotIn("local Codex CLI in `workspace/`", bootstrap.INTRO)


if __name__ == "__main__":
    unittest.main()
