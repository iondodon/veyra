import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from providers import ProviderResponse, ShellAction, ShellCall


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.emitter = patch.object(bootstrap, "emit", side_effect=lambda event_type, **fields: self.events.append({"type": event_type, **fields}))
        self.emitter.start()
        self.addCleanup(self.emitter.stop)
        self.chat = bootstrap.Conversation()
        self.chat.provider = Mock(name="provider")
        self.call = ShellCall(call_id="call-1", action=ShellAction(commands=["printf hello"]))
        self.chat.provider.create_response.side_effect = [
            ProviderResponse(id="r1", output=[self.call], output_text="I'll check."),
            ProviderResponse(id="r2", output=[], output_text="Finished."),
        ]

    def request_commands(self):
        self.chat.handle("Please check")
        return self.chat.pending[2]

    def test_commands_wait_for_matching_approval_and_execute_once(self):
        with patch.object(bootstrap, "run_local", return_value={"stdout": "hello", "stderr": "", "outcome": {"type": "exit", "exit_code": 0}}) as run:
            approval = self.request_commands()
            run.assert_not_called()
            self.chat.handle("/approve", "stale-approval")
            run.assert_not_called()
            self.chat.handle("/approve", approval)
            run.assert_called_once_with("printf hello")
            self.chat.handle("/approve", approval)
            run.assert_called_once()
        self.assertEqual(self.chat.previous_response_id, "r2")
        self.assertTrue(any(e.get("text") == "Finished." for e in self.events))
        continuation = self.chat.provider.create_response.call_args.kwargs
        self.assertEqual(continuation["input"][0]["call_id"], "call-1")
        self.assertEqual(continuation["input"][0]["output"][0]["stdout"], "hello")

    def test_denial_and_ordinary_chat_do_not_execute_commands(self):
        with patch.object(bootstrap, "run_local") as run:
            approval = self.request_commands()
            self.chat.handle("Do something else")
            self.assertEqual(self.chat.pending[2], approval)
            self.chat.handle("/deny", approval)
            run.assert_not_called()
            self.assertIsNone(self.chat.pending)

    def test_switching_provider_cancels_old_approval_and_context(self):
        approval = self.request_commands()
        replacement = Mock(name="new_provider", model="test-model")
        with patch.object(bootstrap, "create_provider", return_value=replacement), \
             patch.object(bootstrap, "save_selected_provider") as save, \
             patch.object(bootstrap, "run_local") as run:
            self.chat.handle("/provider chatgpt")
            save.assert_called_once_with(bootstrap.PROVIDER_FILE, "chatgpt")
            self.chat.handle("/approve", approval)
            run.assert_not_called()
        self.assertIs(self.chat.provider, replacement)
        self.assertIsNone(self.chat.previous_response_id)

    def test_connect_sends_signin_link_to_chat_and_selects_plan(self):
        with patch.object(bootstrap, "ChatGPTAuth") as auth, \
             patch.object(self.chat, "select_provider") as select:
            self.chat.handle("/connect")
            auth.return_value.login.assert_called_once_with(open_browser=False, notify=bootstrap.send)
            select.assert_called_once_with("chatgpt")

    def test_provider_failures_do_not_change_billing_or_selection(self):
        original = self.chat.provider
        with patch.object(bootstrap, "create_provider", side_effect=bootstrap.ProviderConfigError("missing key")), \
             patch.object(bootstrap, "save_selected_provider") as save:
            with self.assertRaises(bootstrap.ProviderConfigError):
                self.chat.handle("/provider openai")
        save.assert_not_called()
        self.assertIs(self.chat.provider, original)


if __name__ == "__main__":
    unittest.main()
