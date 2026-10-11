import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_browser


class HostBrowserTests(unittest.TestCase):
    def test_opens_on_host_without_passing_api_keys_or_token_overrides(self):
        with patch.dict(os.environ, {"DISPLAY": ":42", "HOME": "/fixture", "OPENAI_API_KEY": "private",
                                     "CLAUDE_CODE_OAUTH_TOKEN": "private"}, clear=True), \
             patch.object(host_browser.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(host_browser.open_sign_in("https://auth.openai.com/fixture"))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][-1], "https://auth.openai.com/fixture")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["DISPLAY"], ":42")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_background_service_uses_current_desktop_session_without_importing_secrets(self):
        with patch.dict(os.environ, {"HOME": "/fixture"}, clear=True), \
             patch.object(host_browser.sys, "platform", "linux"), \
             patch.object(host_browser.subprocess, "run", side_effect=[
                 SimpleNamespace(returncode=0, stdout="DISPLAY=:42\nWAYLAND_DISPLAY=wayland-1\nOPENAI_API_KEY=private\n"),
                 SimpleNamespace(returncode=0)]) as run:
            self.assertTrue(host_browser.open_sign_in("https://claude.com/cai/oauth/authorize?state=fixture"))
        self.assertEqual(run.call_args_list[0].args[0], ["systemctl", "--user", "show-environment"])
        env = run.call_args_list[1].kwargs["env"]
        self.assertEqual(env["DISPLAY"], ":42")
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-1")
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_headless_server_returns_failure_without_starting_a_browser(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(host_browser.sys, "platform", "linux"), \
             patch.object(host_browser.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="HOME=/fixture\n")) as run:
            self.assertFalse(host_browser.open_sign_in("https://auth.openai.com/fixture"))
        run.assert_called_once()

    def test_missing_session_manager_is_handled(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(host_browser.sys, "platform", "linux"), \
             patch.object(host_browser.subprocess, "run", side_effect=OSError("unavailable")):
            self.assertFalse(host_browser.open_sign_in("https://auth.openai.com/fixture"))

    def test_browser_failure_and_timeout_are_handled(self):
        for result in (SimpleNamespace(returncode=1), subprocess.TimeoutExpired("browser", 10)):
            with self.subTest(result=result), patch.dict(os.environ, {"DISPLAY": ":42"}, clear=True), \
                 patch.object(host_browser.subprocess, "run", side_effect=[result]):
                self.assertFalse(host_browser.open_sign_in("https://auth.openai.com/fixture"))
