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
    (path / "stat").write_text(
        f"{pid} (process) S {ppid} {pid} {pid} {tty} {pid} 0 0\n",
        encoding="utf-8",
    )
    (path / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


class ClaudeBridgeTests(unittest.TestCase):
    def test_prompt_is_staged_in_existing_claude_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            add_process(proc, 100, 1, ["/usr/bin/ghostty"], tty=0)
            add_process(proc, 200, 100, ["/home/me/.local/bin/claude"])
            observed = []

            def runner(command, **kwargs):
                observed.append((command, kwargs))
                if command[-2:] == ["--json", "windows"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps([{"id": 9, "pid": 100, "is_focused": True}]),
                        stderr="",
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            window = bootstrap.stage_prompt_in_open_claude(
                "review this; echo $HOME",
                which=lambda name: "/usr/bin/" + name,
                runner=runner,
                proc_root=proc,
            )

        self.assertEqual(window, 9)
        self.assertEqual(observed[1][0][-3:], ["focus-window", "--id", "9"])
        self.assertEqual(
            observed[2][0],
            ["/usr/bin/wtype", "-s", "150", "-d", "25", "-", "-s", "300"],
        )
        self.assertEqual(observed[2][1]["input"], "review this; echo $HOME")
        self.assertNotIn("Return", observed[2][0])
        self.assertFalse(any("exec" in command for command, kwargs in observed))

    def test_npm_claude_code_process_is_recognized(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            add_process(proc, 100, 1, ["ghostty"], tty=0)
            add_process(
                proc, 200, 100,
                ["node", "/lib/node_modules/@anthropic-ai/claude-code/cli.js"],
            )
            windows = [{"id": 4, "pid": 100, "is_focused": True}]
            self.assertEqual(bootstrap.find_open_claude_window(windows, proc), 4)

    def test_codex_process_is_not_mistaken_for_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            add_process(proc, 100, 1, ["ghostty"], tty=0)
            add_process(proc, 200, 100, ["node", "/home/me/bin/codex"])
            windows = [{"id": 4, "pid": 100, "is_focused": True}]
            with self.assertRaisesRegex(bootstrap.ClaudeError, "No open interactive"):
                bootstrap.find_open_claude_window(windows, proc)

    def test_focused_window_wins_when_multiple_claude_sessions_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            for terminal, claude in ((100, 200), (101, 201)):
                add_process(proc, terminal, 1, ["ghostty"], tty=0)
                add_process(proc, claude, terminal, ["claude"])
            windows = [
                {"id": 7, "pid": 100, "is_focused": False},
                {"id": 8, "pid": 101, "is_focused": True},
            ]
            self.assertEqual(bootstrap.find_open_claude_window(windows, proc), 8)

    def test_empty_prompt_is_rejected_before_desktop_inspection(self):
        with self.assertRaisesRegex(bootstrap.ClaudeError, "prompt is required"):
            bootstrap.stage_prompt_in_open_claude("  ")

    def test_missing_desktop_tools_has_clear_error(self):
        with self.assertRaisesRegex(bootstrap.ClaudeError, "niri and wtype"):
            bootstrap.stage_prompt_in_open_claude("hello", which=lambda name: None)

    def test_intro_and_handler_expose_claude_command(self):
        self.assertIn("/claude your prompt", bootstrap.INTRO)
        source = Path(bootstrap.__file__).read_text(encoding="utf-8")
        self.assertIn('text.startswith("/claude ")', source)
        self.assertIn("stage_prompt_in_open_claude(prompt)", source)


if __name__ == "__main__":
    unittest.main()
