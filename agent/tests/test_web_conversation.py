import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from providers import ProviderResponse, ShellAction, ShellCall


class ConversationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = Path(directory.name) / "state"
        for name, value in (("STATE", state), ("PROVIDER_FILE", state / "memory/provider.json")):
            patched = patch.object(bootstrap, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.events = []
        self.emitter = patch.object(bootstrap, "emit", side_effect=lambda event_type, **fields: self.events.append({"type": event_type, **fields}))
        self.emitter.start()
        self.addCleanup(self.emitter.stop)
        browser = patch.object(bootstrap, "open_sign_in", return_value=True)
        self.browser = browser.start()
        self.addCleanup(browser.stop)
        self.chat = bootstrap.Conversation()
        self.chat.provider = Mock(name="provider")
        self.chat.provider.name = "chatgpt"
        self.chat.provider.model = "test-model"
        self.call = ShellCall(call_id="call-1", action=ShellAction(commands=["printf hello"]))
        self.chat.provider.create_response.side_effect = [
            ProviderResponse(id="r1", output=[self.call], output_text="I'll check."),
            ProviderResponse(id="r2", output=[], output_text="Finished."),
        ]

    def test_image_message_reaches_model_as_multimodal_content(self):
        from test_images import IMAGE
        self.chat.handle("Describe this", images=[IMAGE])
        content = self.chat.provider.create_response.call_args.kwargs['input'][0]['content']
        self.assertEqual(content[0], {'type': 'input_text', 'text': 'Describe this'})
        self.assertEqual(content[1], {'type': 'input_image', 'image_url': IMAGE['data_url']})

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
        replacement.name = "chatgpt"
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
            auth.return_value.login.side_effect = lambda **kwargs: kwargs["notify"](
                "Continue with ChatGPT\nhttps://auth.openai.com/fixture")
            self.chat.handle("/connect")
            thread = self.chat.login_thread
            if thread:
                thread.join(timeout=2)
            auth.return_value.login.assert_called_once()
            options = auth.return_value.login.call_args.kwargs
            self.assertFalse(options["open_browser"])
            self.browser.assert_called_once_with("https://auth.openai.com/fixture")
            self.assertIs(options["cancel_event"], self.chat.login_cancel)
            select.assert_called_once_with("chatgpt")
            self.assertTrue(any(event.get("url") == "https://auth.openai.com/fixture" for event in self.events))

    def test_remembered_signins_wait_for_explicit_click_before_opening_host_browser(self):
        for service, auth_name, connect_name, url in (
                ("chatgpt", "ChatGPTAuth", "connect_subscription", "https://auth.openai.com/fixture"),
                ("claude", "ClaudeSubscriptionAuth", "connect_claude_subscription", "https://claude.com/cai/oauth/authorize?state=fixture")):
            with self.subTest(service=service):
                self.chat = bootstrap.Conversation()
                self.browser.reset_mock()
                bootstrap.save_selected_provider(bootstrap.PROVIDER_FILE, service)
                ready = threading.Event()
                def login(**options):
                    options["notify"](("Continue with ChatGPT\n" if service == "chatgpt" else "") + url)
                    ready.set()
                    options["cancel_event"].wait(timeout=3)
                with patch.object(bootstrap, auth_name) as auth, \
                     patch.object(bootstrap, "create_provider", side_effect=bootstrap.ProviderConfigError("not signed in")), \
                     patch.object(bootstrap, "provider_available", return_value=False):
                    auth.return_value.login.side_effect = login
                    try:
                        self.chat.start()
                        self.assertTrue(ready.wait(timeout=2))
                        self.browser.assert_not_called()
                        getattr(self.chat, connect_name)()
                        self.browser.assert_called_once_with(url)
                    finally:
                        cancelled = self.chat.login_cancel if service == "chatgpt" else self.chat.claude_login_cancel
                        thread = self.chat.login_thread if service == "chatgpt" else self.chat.claude_login_thread
                        cancelled.set()
                        if thread:
                            thread.join(timeout=2)

    def test_click_before_signin_url_is_ready_opens_browser_when_preparation_finishes(self):
        waiting, release, opened = threading.Event(), threading.Event(), threading.Event()
        def login(**options):
            waiting.set()
            release.wait(timeout=3)
            options["notify"]("Continue with ChatGPT\nhttps://auth.openai.com/fixture")
            opened.set()
            options["cancel_event"].wait(timeout=3)
        with patch.object(bootstrap, "ChatGPTAuth") as auth:
            auth.return_value.login.side_effect = login
            try:
                self.chat.connect_subscription(open_browser=False)
                self.assertTrue(waiting.wait(timeout=2))
                self.chat.connect_subscription()
                self.browser.assert_not_called()
                release.set()
                self.assertTrue(opened.wait(timeout=2))
                self.browser.assert_called_once_with("https://auth.openai.com/fixture")
            finally:
                self.chat.login_cancel.set()
                release.set()
                thread = self.chat.login_thread
                if thread:
                    thread.join(timeout=2)

    def test_unavailable_host_browser_is_reported_without_exposing_signin_url(self):
        self.browser.return_value = False
        self.chat.open_subscription_browser("chatgpt", "https://auth.openai.com/fixture")
        self.assertEqual(self.events[-1]["kind"], "error")
        self.assertIn("computer running Veyra", self.events[-1]["text"])
        self.assertNotIn("https://", self.events[-1]["text"])

    def test_provider_failures_do_not_change_billing_or_selection(self):
        original = self.chat.provider
        with patch.object(bootstrap, "create_provider", side_effect=bootstrap.ProviderConfigError("missing key")), \
             patch.object(bootstrap, "save_selected_provider") as save:
            with self.assertRaises(bootstrap.ProviderConfigError):
                self.chat.handle("/provider openai")
        save.assert_not_called()
        self.assertIs(self.chat.provider, original)

    def test_first_start_waits_for_choice_without_authentication_or_model_calls(self):
        self.chat.provider = None
        self.chat.credentials.save("openai", "sk-fixture-available-but-unselected")
        with patch.object(bootstrap, "create_provider") as create, \
             patch.object(bootstrap, "ChatGPTAuth") as chatgpt, \
             patch.object(bootstrap, "ClaudeSubscriptionAuth") as claude, \
             patch.object(bootstrap, "save_selected_provider") as save:
            self.chat.start()
            self.chat.handle("Hello")
        create.assert_not_called()
        chatgpt.assert_not_called()
        claude.assert_not_called()
        save.assert_not_called()
        self.assertIsNone(self.chat.selected)
        self.assertIsNone(self.chat.provider)
        self.assertTrue(any(event["type"] == "ready" for event in self.events))
        configuration = next(e for e in self.events if e["type"] == "configuration")
        self.assertIsNone(configuration["provider"])
        self.assertIsNone(configuration["model"])
        self.assertFalse(configuration["connected"])

    def test_unspecified_subscription_request_offers_both_services(self):
        self.chat.provider = None
        with patch.object(self.chat, "connect_subscription") as chatgpt, \
             patch.object(self.chat, "connect_claude_subscription") as claude:
            for phrase in ("Sign in", "Sign me in", "Use my subscription", "Use subscription mode"):
                self.chat.handle(phrase)
        chatgpt.assert_not_called()
        claude.assert_not_called()
        self.assertIsNone(self.chat.selected)
        replies = [e for e in self.events if e.get("kind") == "setup"]
        self.assertEqual(len(replies), 4)
        self.assertTrue(all("ChatGPT" in e["text"] and "Claude" in e["text"] for e in replies))

    def test_explicit_chatgpt_choice_starts_background_signin(self):
        self.chat.provider = None
        finish = threading.Event()
        link_ready = threading.Event()
        connected = SimpleNamespace(name="chatgpt", model="preset-plan-model")
        def login(**kwargs):
            kwargs["notify"]("Continue with ChatGPT\nhttps://auth.openai.com/fixture")
            link_ready.set()
            finish.wait(timeout=3)
        with patch.object(bootstrap, "create_provider", return_value=connected) as create, \
             patch.object(bootstrap, "provider_available", return_value=False), \
             patch.object(bootstrap, "ChatGPTAuth") as auth:
            auth.return_value.login.side_effect = login
            try:
                self.chat.start()
                self.assertIsNone(self.chat.selected)
                self.assertFalse(link_ready.is_set())
                self.chat.handle("Use my ChatGPT subscription.")
                self.assertTrue(link_ready.wait(timeout=2))
                self.assertEqual(self.chat.selected, "chatgpt")
                self.assertTrue(any(event["type"] == "ready" for event in self.events))
                self.assertTrue(self.chat.login_thread.is_alive())
                self.chat.handle("What model are you using?")
            finally:
                thread = self.chat.login_thread
                finish.set()
                if thread:
                    thread.join(timeout=2)
            self.assertEqual([call.args[0] for call in create.call_args_list], ["chatgpt"])
        self.assertIs(self.chat.provider, connected)
        self.assertTrue(any("preset-plan-model" in event.get("text", "") for event in self.events))

    def test_finishing_signin_does_not_override_an_explicit_api_choice(self):
        self.chat.provider = None
        finish = threading.Event()
        link_ready = threading.Event()
        def login(**kwargs):
            kwargs["notify"]("Continue with ChatGPT\nhttps://auth.openai.com/fixture")
            link_ready.set()
            finish.wait(timeout=3)
        self.chat.credentials.save("openai", "sk-fixture-saved-api-key")
        api = SimpleNamespace(name="openai", model="preset-api-model")
        with patch.object(bootstrap, "ChatGPTAuth") as auth, \
             patch.object(bootstrap, "create_provider", return_value=api):
            auth.return_value.login.side_effect = login
            try:
                self.chat.connect_subscription()
                self.assertTrue(link_ready.wait(timeout=2))
                self.chat.handle("I want to use the OpenAI API.")
            finally:
                thread = self.chat.login_thread
                finish.set()
                if thread:
                    thread.join(timeout=2)
        self.assertEqual(self.chat.selected, "openai")
        self.assertIs(self.chat.provider, api)

    def test_remembered_api_mode_uses_the_durable_key_and_preset_model(self):
        self.chat.provider = None
        self.chat.credentials.save("anthropic", "sk-ant-fixture-persistent-key")
        bootstrap.save_selected_provider(bootstrap.PROVIDER_FILE, "anthropic")
        api = SimpleNamespace(name="anthropic", model="preset-api-model")
        with patch.object(bootstrap, "create_provider", return_value=api) as create, \
             patch.object(self.chat, "connect_subscription") as connect:
            self.chat.start()
        connect.assert_not_called()
        self.assertEqual(create.call_args.args[0], "anthropic")
        self.assertEqual(create.call_args.args[1]["ANTHROPIC_API_KEY"], "sk-ant-fixture-persistent-key")
        self.assertTrue(any("preset-api-model" in event.get("text", "") for event in self.events))
        self.assertNotIn("sk-ant-fixture-persistent-key", str(self.events))

    def test_missing_api_key_opens_private_form_without_billing_switch(self):
        original = self.chat.provider
        with patch.object(self.chat, "environment", return_value={}), patch.object(bootstrap, "create_provider") as create:
            self.chat.handle("Please use the Anthropic API.")
            self.chat.handle("I want to add my OpenAI API key.")
        create.assert_not_called()
        self.assertIs(self.chat.provider, original)
        self.assertEqual([e.get("provider") for e in self.events if e.get("kind") == "key_request"], ["anthropic", "openai"])

    def test_natural_language_approvals_keep_the_matching_request_requirement(self):
        with patch.object(bootstrap, "run_local", return_value={"stdout": "hello", "stderr": "", "outcome": {"type": "exit", "exit_code": 0}}) as run:
            approval = self.request_commands()
            self.chat.handle("Approve these commands.", "wrong")
            run.assert_not_called()
            self.chat.handle("Approve these commands.", approval)
            run.assert_called_once_with("printf hello")

    def test_rotated_key_is_loaded_before_the_next_api_request(self):
        self.chat.provider.name = "openai"
        self.chat.selected = "openai"
        self.chat.active_api_key = "old-private-key"
        self.chat.previous_response_id = "old-response"
        self.chat.credentials.save("openai", "sk-fixture-new-api-key")
        api = Mock(model="preset-api-model")
        api.name = "openai"
        api.create_response.return_value = ProviderResponse(id="fresh-response", output=[], output_text="Hello")
        with patch.object(bootstrap, "create_provider", return_value=api) as create:
            self.chat.handle("Hello")
        self.assertEqual(create.call_args.args[1]["OPENAI_API_KEY"], "sk-fixture-new-api-key")
        self.assertNotIn("previous_response_id", api.create_response.call_args.kwargs)
        self.assertNotIn("sk-fixture-new-api-key", str(self.events))

    def test_private_keys_in_command_output_are_redacted_before_model_continuation(self):
        key = "fixture-private-key-without-standard-prefix"
        self.chat.credentials.save("openai", key)
        with patch.object(bootstrap, "run_local", return_value={"stdout": key, "stderr": key,
                          "outcome": {"type": "exit", "exit_code": 0}}):
            approval = self.request_commands()
            self.chat.handle("Approve these commands.", approval)
        continuation = self.chat.provider.create_response.call_args.kwargs
        self.assertNotIn(key, str(continuation))
        self.assertNotIn(key, str(self.events))
        self.assertIn("API key hidden", str(continuation))

    def test_claude_and_anthropic_subscription_phrases_select_subscription(self):
        for phrase in ("Use my Claude subscription.", "Use my Anthropic subscription.",
                       "Please switch to Claude.", "I want to use the Anthropic plan."):
            with patch.object(bootstrap, "provider_available", return_value=False), \
                 patch.object(self.chat, "connect_claude_subscription") as connect, \
                 patch.object(self.chat, "request_key") as keys:
                self.chat.handle(phrase)
            connect.assert_called_once()
            keys.assert_not_called()
        with patch.object(self.chat, "connect_claude_subscription") as connect:
            self.chat.handle("Sign in with Claude.")
        connect.assert_called_once()

    def test_claude_signin_is_async_and_keeps_later_service_choice(self):
        finish = threading.Event()
        link_ready = threading.Event()
        url = "https://claude.com/cai/oauth/authorize?state=fixture"
        def login(**kwargs):
            kwargs["notify"](url)
            link_ready.set()
            finish.wait(timeout=3)
        with patch.object(bootstrap, "ClaudeSubscriptionAuth") as auth, \
             patch.object(self.chat, "select_provider") as select:
            auth.return_value.login.side_effect = login
            try:
                self.chat.handle("Sign in with Claude.")
                self.assertTrue(link_ready.wait(timeout=2))
                self.assertEqual(self.chat.selected, "claude")
                self.assertTrue(self.chat.claude_login_thread.is_alive())
                self.chat.selected = "openai"
                self.chat.sign_in_notice("Continue with ChatGPT\nhttps://auth.openai.com/stale")
                self.assertFalse(any(e.get("url") == "https://auth.openai.com/stale" for e in self.events))
            finally:
                thread = self.chat.claude_login_thread
                finish.set()
                if thread:
                    thread.join(timeout=2)
            select.assert_not_called()
        self.assertEqual(self.chat.selected, "openai")
        self.assertIsNone(self.chat.claude_login_url)
        self.assertTrue(any(e.get("url") == url and e.get("provider") == "claude" for e in self.events))

    def test_remembered_claude_subscription_reuses_signin_and_ignores_api_store(self):
        bootstrap.save_selected_provider(bootstrap.PROVIDER_FILE, "claude")
        claude = SimpleNamespace(name="claude", model="sonnet")
        with patch.object(bootstrap, "create_provider", return_value=claude) as create, \
             patch.object(self.chat, "environment", side_effect=AssertionError("API keys must not be read")), \
             patch.object(self.chat, "connect_claude_subscription") as connect:
            self.chat.start()
        connect.assert_not_called()
        self.assertEqual(create.call_args.args[0], "claude")
        self.assertEqual(create.call_args.args[1]["VEYRA_CLAUDE_CONFIG_DIR"], str(bootstrap.STATE / "auth/claude"))
        self.assertTrue(any("your Claude subscription" in e.get("text", "") for e in self.events))

    def test_claude_shell_proposals_still_require_matching_approval(self):
        self.chat.provider.name = "claude"
        with patch.object(bootstrap, "run_local", return_value={"stdout": "hello", "stderr": "", "outcome": {"type": "exit", "exit_code": 0}}) as run:
            approval = self.request_commands()
            run.assert_not_called()
            self.chat.handle("Approve these commands.", "old-id")
            run.assert_not_called()
            self.chat.handle("Approve these commands.", approval)
            run.assert_called_once_with("printf hello")

    def test_claude_signin_completion_selects_subscription_and_preset_model(self):
        finish = threading.Event()
        link_ready = threading.Event()
        def login(**kwargs):
            kwargs["notify"]("https://claude.com/cai/oauth/authorize?state=fixture")
            link_ready.set()
            finish.wait(timeout=3)
        with patch.object(bootstrap, "ClaudeSubscriptionAuth") as auth, \
             patch.object(self.chat, "select_provider") as select:
            auth.return_value.login.side_effect = login
            try:
                self.chat.connect_claude_subscription()
                self.assertTrue(link_ready.wait(timeout=2))
            finally:
                thread = self.chat.claude_login_thread
                finish.set()
                if thread:
                    thread.join(timeout=2)
            select.assert_called_once_with("claude")

    def assert_setup_choices(self, text):
        for expected in ("ChatGPT", "Claude", "OpenAI API", "Anthropic API", "Settings → API keys"):
            self.assertIn(expected, text)

    def test_disconnected_guidance_is_the_same_for_every_previous_service(self):
        self.chat.provider = None
        for selected in (None, "chatgpt", "claude", "openai", "anthropic"):
            self.chat.selected = selected
            self.assert_setup_choices(self.chat.provider_status())
        for help_text in (bootstrap.LOGIN_HELP, bootstrap.CLAUDE_LOGIN_HELP):
            self.assert_setup_choices(bootstrap.chat_error(RuntimeError(help_text)))
        self.assertIn("Settings", bootstrap.INTRO)

    def test_chatgpt_logout_clears_selection_context_and_approval_without_model_or_shell(self):
        original = self.chat.provider
        self.chat.selected = "chatgpt"
        self.chat.previous_response_id = "old-response"
        self.chat.pending = (None, [self.call], "old-approval")
        bootstrap.save_selected_provider(bootstrap.PROVIDER_FILE, "chatgpt")
        with patch.object(bootstrap, "ChatGPTAuth") as auth, patch.object(bootstrap, "run_local") as run:
            auth.return_value.logout.return_value = "Signed out of ChatGPT."
            self.chat.handle("Please log me out.")
            self.chat.handle("Hi")
        auth.return_value.logout.assert_called_once()
        original.create_response.assert_not_called()
        run.assert_not_called()
        self.assertIsNone(self.chat.provider)
        self.assertIsNone(self.chat.selected)
        self.assertIsNone(self.chat.pending)
        self.assertIsNone(self.chat.previous_response_id)
        self.assertIsNone(bootstrap.load_selected_provider(bootstrap.PROVIDER_FILE))
        self.assert_setup_choices(self.events[-1]["text"])
        self.assertEqual(self.events[-1]["kind"], "setup")

    def test_api_disconnect_retains_saved_keys_and_does_not_start_signin(self):
        self.chat.provider.name = "openai"
        self.chat.selected = "openai"
        self.chat.credentials.save("openai", "sk-fixture-key-retained-after-disconnect")
        bootstrap.save_selected_provider(bootstrap.PROVIDER_FILE, "openai")
        with patch.object(bootstrap, "ChatGPTAuth") as chatgpt, patch.object(bootstrap, "ClaudeSubscriptionAuth") as claude:
            self.chat.handle("Disconnect current service.")
        chatgpt.assert_not_called()
        claude.assert_not_called()
        self.assertIsNone(self.chat.selected)
        self.assertIsNone(bootstrap.load_selected_provider(bootstrap.PROVIDER_FILE))
        self.assertEqual(self.chat.credentials.environment({})["OPENAI_API_KEY"], "sk-fixture-key-retained-after-disconnect")
        self.assert_setup_choices(self.events[-1]["text"])

    def test_signing_out_an_inactive_subscription_preserves_explicit_api_mode(self):
        original = self.chat.provider
        original.name = "openai"
        self.chat.selected = "openai"
        bootstrap.save_selected_provider(bootstrap.PROVIDER_FILE, "openai")
        with patch.object(bootstrap, "ChatGPTAuth") as auth:
            auth.return_value.logout.return_value = "Signed out of ChatGPT."
            self.chat.handle("Log out of my ChatGPT account.")
        auth.return_value.logout.assert_called_once()
        self.assertIs(self.chat.provider, original)
        self.assertEqual(self.chat.selected, "openai")
        self.assertEqual(bootstrap.load_selected_provider(bootstrap.PROVIDER_FILE), "openai")

    def test_external_logout_error_clears_stale_provider_and_returns_all_choices(self):
        self.chat.selected = "chatgpt"
        bootstrap.save_selected_provider(bootstrap.PROVIDER_FILE, "chatgpt")
        with patch.object(bootstrap, "provider_available", return_value=False):
            self.chat.report_error(bootstrap.ChatGPTError("ChatGPT is not signed in. " + bootstrap.LOGIN_HELP))
        self.assertIsNone(self.chat.provider)
        self.assertIsNone(self.chat.selected)
        self.assertIsNone(bootstrap.load_selected_provider(bootstrap.PROVIDER_FILE))
        self.assert_setup_choices(self.events[-1]["text"])
        self.assertEqual(self.events[-1]["kind"], "setup")

    def test_temporary_request_error_preserves_connection_and_authentication(self):
        original = self.chat.provider
        self.chat.selected = "chatgpt"
        with patch.object(bootstrap, "provider_available") as available:
            self.chat.report_error(bootstrap.ChatGPTError("Connection interrupted. Try again later."))
        available.assert_not_called()
        self.assertIs(self.chat.provider, original)
        self.assertEqual(self.chat.selected, "chatgpt")

    def test_logout_cancels_pending_chatgpt_signin_and_ignores_late_completion(self):
        link_ready = threading.Event()
        def login(**options):
            options["notify"]("Continue with ChatGPT\nhttps://auth.openai.com/fixture")
            link_ready.set()
            options["cancel_event"].wait(timeout=3)
            options["notify"]("Continue with ChatGPT\nhttps://auth.openai.com/late")
        with patch.object(bootstrap, "ChatGPTAuth") as auth, patch.object(self.chat, "select_provider") as select:
            auth.return_value.login.side_effect = login
            auth.return_value.logout.return_value = "Signed out of ChatGPT."
            self.chat.connect_subscription()
            self.assertTrue(link_ready.wait(timeout=2))
            thread = self.chat.login_thread
            self.chat.handle("Log out.")
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            select.assert_not_called()
        self.assertIsNone(self.chat.selected)
        self.assertIsNone(self.chat.login_url)
        self.assertFalse(any(e.get("url") == "https://auth.openai.com/late" for e in self.events))
        self.assert_setup_choices(self.events[-1]["text"])

    def test_logout_cancels_pending_claude_signin_and_ignores_late_completion(self):
        link_ready = threading.Event()
        def login(**options):
            options["notify"]("https://claude.com/cai/oauth/authorize?state=fixture")
            link_ready.set()
            options["cancel_event"].wait(timeout=3)
            options["notify"]("https://claude.com/cai/oauth/authorize?state=late")
        with patch.object(bootstrap, "ClaudeSubscriptionAuth") as auth, patch.object(self.chat, "select_provider") as select:
            auth.return_value.login.side_effect = login
            auth.return_value.logout.return_value = "Signed out of Claude."
            self.chat.connect_claude_subscription()
            self.assertTrue(link_ready.wait(timeout=2))
            thread = self.chat.claude_login_thread
            self.chat.handle("Sign out.")
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            select.assert_not_called()
            auth.return_value.cancel_login.assert_called_once()
        self.assertIsNone(self.chat.selected)
        self.assertIsNone(self.chat.claude_login_url)
        self.assertFalse(any("late" in e.get("url", "") for e in self.events))
        self.assert_setup_choices(self.events[-1]["text"])


if __name__ == "__main__":
    unittest.main()
