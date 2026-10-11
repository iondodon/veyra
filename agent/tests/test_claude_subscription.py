import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_subscription import ClaudeSubscriptionAuth, ClaudeSubscriptionProvider
from providers import ProviderConfigError, create_provider


class ClaudeSubscriptionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env = {"HOME": str(self.root), "PATH": os.environ.get("PATH", ""),
                    "VEYRA_CLAUDE_BIN": sys.executable,
                    "VEYRA_CLAUDE_CONFIG_DIR": str(self.root / "auth/claude")}
        self.auth = ClaudeSubscriptionAuth(self.env)

    def provider(self):
        with patch.object(self.auth, "signed_in", return_value=True):
            return ClaudeSubscriptionProvider(auth=self.auth)

    def reply(self, text="Hello", commands=None, **fields):
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "is_error": False, "structured_output": {"text": text, "commands": commands or []},
            "modelUsage": {"claude-sonnet-fixture": {}}, **fields}), stderr="")

    def test_subscription_environment_excludes_api_and_token_overrides(self):
        env = {**self.env, "ANTHROPIC_API_KEY": "secret-api-key", "OPENAI_API_KEY": "other-key",
               "ANTHROPIC_AUTH_TOKEN": "secret-token", "CLAUDE_CODE_OAUTH_TOKEN": "override",
               "ANTHROPIC_BASE_URL": "https://wrong.example", "CLAUDE_CODE_USE_BEDROCK": "1",
               "CLAUDE_CONFIG_DIR": "/wrong/global/store", "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "100"}
        auth = ClaudeSubscriptionAuth(env)
        self.assertFalse(any(name.startswith(("ANTHROPIC_", "OPENAI_", "CLAUDE_CODE_USE_", "CLAUDE_CODE_OAUTH_")) for name in auth.env))
        self.assertEqual(auth.env["CLAUDE_CONFIG_DIR"], str(self.root / "auth/claude"))
        self.assertEqual(auth.directory.stat().st_mode & 0o777, 0o700)

    def test_only_claude_subscription_authentication_is_accepted(self):
        for method in ("api_key", "api_key_helper", "third_party", "oauth_token", "none", "claude.ai"):
            result = SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": True, "authMethod": method}))
            with patch("claude_subscription.subprocess.run", return_value=result) as run:
                self.assertEqual(self.auth.signed_in(), method == "claude.ai")
            self.assertIn("--safe-mode", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["umask"], 0o077)

    def test_expired_or_invalid_status_is_not_a_subscription(self):
        with patch("claude_subscription.subprocess.run", return_value=SimpleNamespace(returncode=1,
                   stdout='{"loggedIn":false,"authMethod":"none"}')):
            self.assertFalse(self.auth.signed_in())
        with patch("claude_subscription.subprocess.run", return_value=SimpleNamespace(returncode=1,
                   stdout="secret credential diagnostics")):
            with self.assertRaisesRegex(ProviderConfigError, "Couldn't check Claude") as error:
                self.auth.signed_in()
            self.assertNotIn("secret", str(error.exception))

    def test_provider_selection_uses_subscription_without_an_api_key(self):
        with patch("claude_subscription.ClaudeSubscriptionAuth.signed_in", return_value=True):
            provider = create_provider("claude", self.env)
        self.assertEqual(provider.name, "claude")
        self.assertEqual(provider.model, "sonnet")

    def test_commands_and_results_round_trip_without_cli_execution_tools(self):
        provider = self.provider()
        with patch.object(self.auth, "signed_in", return_value=True), \
             patch("claude_subscription.subprocess.run", side_effect=[self.reply("I'll check.", ["pwd"]), self.reply("Done")]) as run:
            first = provider.create_response(instructions="Veyra instructions", input="Where are we?", tools=[{"type": "shell"}])
            self.assertEqual(first.output[0].action.commands, ["pwd"])
            output = [{"type": "shell_call_output", "call_id": first.output[0].call_id,
                       "output": [{"stdout": "/fixture", "stderr": "", "outcome": {"type": "exit", "exit_code": 0}}]}]
            second = provider.create_response(instructions="Veyra instructions", input=output,
                                              previous_response_id=first.id, tools=[{"type": "shell"}])
        self.assertEqual(second.output_text, "Done")
        self.assertEqual(second.output, [])
        self.assertEqual(provider.model, "claude-sonnet-fixture")
        command = run.call_args.args[0]
        self.assertIn("--safe-mode", command)
        self.assertIn("--restricted", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(command[command.index("--mcp-config") + 1], '{"mcpServers":{}}')
        self.assertNotIn("--bare", command)
        self.assertNotIn("--dangerously-skip-permissions", command)
        history = json.loads(run.call_args.kwargs["input"])["conversation"]
        self.assertEqual(history[0]["content"], "Where are we?")
        self.assertEqual(history[1]["content"]["commands"], ["pwd"])
        self.assertEqual(history[2]["content"], output)

    def test_failed_or_malformed_replies_do_not_advance_history_or_expose_logs(self):
        provider = self.provider()
        failures = [self.reply(is_error=True), self.reply(commands=[None]),
                    self.reply(commands=[""]), self.reply(structured_output={"text": "Missing commands"}),
                    SimpleNamespace(returncode=1, stdout="secret-cli-output", stderr="secret-cli-stderr")]
        for reply in failures:
            with patch.object(self.auth, "signed_in", return_value=True), \
                 patch("claude_subscription.subprocess.run", return_value=reply):
                with self.assertRaises(ProviderConfigError) as error:
                    provider.create_response(instructions="Veyra", input="Hello", tools=[{"type": "shell"}])
            self.assertEqual(len(provider._threads), 0)
            self.assertNotIn("secret", str(error.exception))

    def test_cli_requests_recheck_subscription_and_never_fall_back_to_api(self):
        provider = self.provider()
        with patch.object(self.auth, "signed_in", return_value=False), \
             patch("claude_subscription.subprocess.run") as run:
            with self.assertRaisesRegex(ProviderConfigError, "Continue with Claude"):
                provider.create_response(instructions="Veyra", input="Hello")
        run.assert_not_called()

    def test_official_login_url_is_forwarded_without_raw_cli_output(self):
        # A real local subprocess exercises pipe parsing, OSC hyperlinks and
        # timeout cleanup. No network calls or real credentials are involved.
        script = self.root / "fixture-cli.py"
        url = "https://claude.com/cai/oauth/authorize?state=fixture&code_challenge=fixture"
        script.write_text("import sys\n"
                          "if 'status' in sys.argv:\n"
                          " print('{\"loggedIn\":true,\"authMethod\":\"claude.ai\"}')\n"
                          "else:\n"
                          " print('Private diagnostic that must not be shown')\n"
                          " print('https://evil.example/oauth/authorize?token=private')\n"
                          f" print('\\x1b]8;;{url}\\x07Continue\\x1b]8;;\\x07')\n")
        self.auth.command = lambda *args: [sys.executable, str(script), *args]
        notices = []
        self.auth.login(notify=notices.append, timeout=3)
        self.assertEqual(notices, [url])

    def test_login_timeout_cleans_up_child_and_reports_retry(self):
        script = self.root / "fixture-timeout.py"
        script.write_text("import time\ntime.sleep(60)\n")
        self.auth.command = lambda *args: [sys.executable, str(script)]
        with self.assertRaisesRegex(ProviderConfigError, "timed out"):
            self.auth.login(notify=lambda _: None, timeout=.1)


if __name__ == "__main__":
    unittest.main()
