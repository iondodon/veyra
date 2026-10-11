from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    import httpx2 as httpx
except ImportError:
    import httpx
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from chatgpt_auth import ChatGPTError
import chatgpt_provider as provider_module
import providers


def response(response_id="resp_1", output=None):
    return {
        "id": response_id, "object": "response", "created_at": 1, "status": "completed",
        "model": "plan-model", "output": output or [], "error": None,
    }


def message(text):
    return {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def shell_call(**changes):
    return {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "shell",
            "namespace": "veyra", "arguments": '{"commands":["pwd"]}', "status": "completed", **changes}


def sse(events):
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events)


class ChatGPTProviderTests(unittest.TestCase):
    def setUp(self):
        self.auth = Mock()
        self.auth.account.return_value = {"client_id": "oaiapp_test", "subject": "user", "email": "owner@example.test"}
        self.auth.access_token.return_value = "subscription-token"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.models = [{"slug": "plan-model", "display_name": "Plan Model"}, {"slug": "other-model"}]
        self.stack.enter_context(patch.object(provider_module, "list_models", return_value=self.models))

    def client(self, replies):
        seen = []
        replies = iter(replies)

        def handle(request):
            seen.append(request)
            reply = next(replies)
            if isinstance(reply, tuple):
                return httpx.Response(reply[0], json=reply[1], headers={"x-request-id": "req_test"})
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(reply))

        def make_client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

        self.stack.enter_context(patch.object(provider_module, "OpenAI", side_effect=make_client))
        return seen

    def test_default_uses_account_catalog_and_explicit_model_must_be_available(self):
        self.assertEqual(provider_module.ChatGPTProvider(auth=self.auth).model, "plan-model")
        self.assertEqual(provider_module.ChatGPTProvider(auth=self.auth, model="other-model").model, "other-model")
        with self.assertRaisesRegex(ChatGPTError, "unavailable"):
            provider_module.ChatGPTProvider(auth=self.auth, model="api-only-model")

    def test_real_sdk_stream_replays_history_and_normalizes_approved_tool_results(self):
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque-reasoning"}
        seen = self.client([
            [{"type": "response.completed", "response": response(output=[reasoning, shell_call()])}],
            [{"type": "response.completed", "response": response("resp_2", [message("Done")])}],
            [{"type": "response.completed", "response": response("resp_3", [message("Yes")])}],
        ])
        provider = provider_module.ChatGPTProvider(auth=self.auth)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "paid-api-key", "OPENAI_ORG_ID": "paid-org", "OPENAI_PROJECT_ID": "paid-project"}):
            first = provider.create_response(instructions="Be helpful", input="Where am I?", tools=bootstrap.SHELL_TOOLS)
            self.assertEqual(first.output[0].action.commands, ["pwd"])
            second = provider.create_response(
                instructions="Be helpful", previous_response_id=first.id, tools=bootstrap.SHELL_TOOLS,
                input=[{"type": "shell_call_output", "call_id": "call_1", "output": [{"stdout": "/workspace", "stderr": ""}]}],
            )
            self.assertEqual(second.output_text, "Done")
            third = provider.create_response(previous_response_id=second.id, input="Remember that?")
            self.assertEqual(third.output_text, "Yes")
        bodies = [json.loads(request.content) for request in seen]
        for request, body in zip(seen, bodies):
            self.assertEqual(str(request.url), "https://api.openai.com/v1/responses")
            self.assertEqual(request.headers["authorization"], "Bearer subscription-token")
            self.assertNotEqual(request.headers.get("openai-organization"), "paid-org")
            self.assertNotEqual(request.headers.get("openai-project"), "paid-project")
            self.assertFalse(body["store"])
            self.assertTrue(body["stream"])
            self.assertNotIn("previous_response_id", body)
            self.assertNotIn("max_output_tokens", body)
        self.assertEqual(bodies[0]["tools"][0]["type"], "namespace")
        self.assertEqual(bodies[1]["input"][1]["encrypted_content"], "opaque-reasoning")
        result = bodies[1]["input"][-1]
        self.assertEqual(result["type"], "function_call_output")
        self.assertEqual(result["call_id"], "call_1")
        self.assertEqual(json.loads(result["output"])[0]["stdout"], "/workspace")
        self.assertEqual(bodies[2]["input"][-1], {"role": "user", "content": "Remember that?"})

    def test_limit_after_streaming_does_not_return_partial_text_or_remember_failure(self):
        seen = self.client([
            [{"type": "response.completed", "response": response(output=[message("Good")])}],
            [{"type": "response.output_text.delta", "delta": "partial", "item_id": "msg_2", "content_index": 0, "output_index": 0},
             {"type": "response.failed", "response": {**response("resp_failed"), "status": "failed", "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}}],
            [{"type": "response.completed", "response": response("resp_3", [message("Recovered")])}],
        ])
        provider = provider_module.ChatGPTProvider(auth=self.auth)
        first = provider.create_response(input="hello")
        with self.assertRaisesRegex(ChatGPTError, "Settings → Usage"):
            provider.create_response(input="failing turn", previous_response_id=first.id)
        provider.create_response(input="retry", previous_response_id=first.id)
        self.assertNotIn("resp_failed", provider._threads)
        self.assertNotIn("failing turn", seen[-1].content.decode())
        self.assertEqual(len(seen), 3)  # No automatic paid retry or fallback.

    def test_finalized_stream_items_survive_empty_terminal_output_and_replay_history(self):
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque-reasoning"}
        reply = {**message("Hello"), "phase": "final_answer"}
        seen = self.client([
            [{"type": "response.output_item.done", "output_index": 0, "item": reasoning},
             {"type": "response.output_item.done", "output_index": 1, "item": reply},
             {"type": "response.completed", "response": response()}],
            [{"type": "response.completed", "response": response("resp_2", [message("Remembered")])}],
        ])
        provider = provider_module.ChatGPTProvider(auth=self.auth)
        first = provider.create_response(input="hello")
        self.assertEqual(first.output_text, "Hello")
        provider.create_response(input="remember?", previous_response_id=first.id)
        history = json.loads(seen[1].content)["input"]
        self.assertEqual(history[1]["encrypted_content"], "opaque-reasoning")
        self.assertEqual(history[2]["phase"], "final_answer")
        self.assertEqual(history[2]["content"][0]["text"], "Hello")

    def test_streamed_tool_call_is_preserved_and_failed_completion_never_requests_execution(self):
        for terminal in (
            {"type": "response.completed", "response": response()},
            {"type": "response.failed", "response": {**response(), "status": "failed",
             "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}},
        ):
            self.client([[{"type": "response.output_item.done", "output_index": 0, "item": shell_call()}, terminal]])
            provider = provider_module.ChatGPTProvider(auth=self.auth)
            if terminal["type"] == "response.completed":
                result = provider.create_response(input="where?", tools=bootstrap.SHELL_TOOLS)
                self.assertEqual(result.output[0].action.commands, ["pwd"])
            else:
                with self.assertRaises(ChatGPTError):
                    provider.create_response(input="where?", tools=bootstrap.SHELL_TOOLS)
                self.assertEqual(provider._threads, {})

    def test_text_events_are_collected_without_duplicate_deltas_or_done_text(self):
        self.client([[
            {"type": "response.output_text.delta", "delta": "Hello", "output_index": 0, "content_index": 0, "item_id": "m"},
            {"type": "response.output_text.delta", "delta": " world", "output_index": 0, "content_index": 0, "item_id": "m"},
            {"type": "response.output_text.done", "text": "Hello world", "output_index": 0, "content_index": 0, "item_id": "m"},
            {"type": "response.completed", "response": response()},
        ]])
        provider = provider_module.ChatGPTProvider(auth=self.auth)
        result = provider.create_response(input="hello")
        self.assertEqual(result.output_text, "Hello world")
        self.assertEqual(provider._threads[result.id][-1], {"role": "assistant", "content": "Hello world"})

    def test_http_errors_keep_status_code_and_request_id(self):
        self.client([(403, {"error": {"code": "subscription_sharing_user_not_eligible", "message": "not eligible"}})])
        provider = provider_module.ChatGPTProvider(auth=self.auth)
        with self.assertRaisesRegex(ChatGPTError, "HTTP 403.*req_test"):
            provider.create_response(input="hello")

    def test_incomplete_interrupted_and_error_streams_never_succeed(self):
        for events in (
            [],
            [{"type": "response.incomplete", "response": {**response(), "status": "incomplete"}}],
            [{"type": "error", "code": "bad_request", "message": "bad", "param": None}],
        ):
            with self.subTest(events=events):
                self.client([events])
                provider = provider_module.ChatGPTProvider(auth=self.auth)
                with self.assertRaises(ChatGPTError):
                    provider.create_response(input="hello")
                self.assertEqual(provider._threads, {})

    def test_unknown_tool_or_invalid_commands_never_become_shell_calls(self):
        for change in ({"name": "delete"}, {"namespace": "other"}, {"arguments": "not-json"},
                       {"arguments": "[]"}, {"arguments": '{"commands":[7]}'},
                       {"arguments": '{"commands":[]}'}, {"arguments": '{"commands":[""]}'}):
            with self.subTest(change=change):
                self.client([[{"type": "response.completed", "response": response(output=[shell_call(**change)])}]])
                provider = provider_module.ChatGPTProvider(auth=self.auth)
                with self.assertRaises(ChatGPTError):
                    provider.create_response(input="hello", tools=bootstrap.SHELL_TOOLS)

    def test_missing_history_is_reported_before_inference(self):
        seen = self.client([])
        provider = provider_module.ChatGPTProvider(auth=self.auth)
        with self.assertRaisesRegex(ChatGPTError, "state expired"):
            provider.create_response(input="hello", previous_response_id="missing")
        self.assertEqual(seen, [])


class WebChatGPTTests(unittest.TestCase):
    def run_messages(self, messages, provider):
        events, executed = [], []

        def incoming():
            for text in messages:
                approvals = [e["approval_id"] for e in events if e["type"] == "approval"]
                yield json.dumps({"text": text, "approval_id": approvals[-1] if approvals else None}) + "\n"

        def execute(command):
            executed.append(command)
            return {"stdout": "ok", "stderr": "", "outcome": {"type": "exit", "exit_code": 0}}

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(bootstrap, "STATE", Path(directory) / "state"), \
                patch.object(bootstrap, "PROVIDER_FILE", Path(directory) / "state/memory/provider.json"), \
                patch.dict(bootstrap.os.environ, {"VEYRA_WEB_TRANSPORT": "1"}, clear=True), \
                patch.object(bootstrap.sys, "argv", ["bootstrap.py"]), \
                patch.object(bootstrap.sys, "stdin", incoming()), \
                patch.object(bootstrap, "load_selected_provider", return_value=None), \
                patch.object(bootstrap, "create_provider", return_value=provider), \
                patch.object(bootstrap, "provider_available", return_value=False), \
                patch.object(bootstrap, "emit", side_effect=lambda event_type, **fields: events.append({"type": event_type, **fields})), \
                patch.object(bootstrap, "run_local", side_effect=execute):
            self.assertEqual(bootstrap.main(), 0)
        return [e["text"] for e in events if "text" in e], executed

    def test_commands_wait_for_owner_approval_and_results_resume_same_provider(self):
        provider = Mock(name="chatgpt-provider", model="plan-model")
        provider.name = "chatgpt"
        first = providers.ProviderResponse("resp_1", [providers.ShellCall("call_1", providers.ShellAction(["pwd"]))], "")
        provider.create_response.side_effect = [first, providers.ProviderResponse("resp_2", [], "Done")]
        sent, executed = self.run_messages(["/provider chatgpt", "Where am I?"], provider)
        self.assertEqual(executed, [])
        self.assertTrue(any("wants to run these commands" in text for text in sent))
        provider.create_response.side_effect = [first, providers.ProviderResponse("resp_2", [], "Done")]
        sent, executed = self.run_messages(["/provider chatgpt", "Where am I?", "/approve"], provider)
        self.assertEqual(executed, ["pwd"])
        self.assertEqual(sent[-1], "Done")
        continuation = provider.create_response.call_args.kwargs
        self.assertEqual(continuation["previous_response_id"], "resp_1")
        self.assertEqual(continuation["input"][0]["call_id"], "call_1")

    def test_plan_limit_is_sent_to_owner_without_a_provider_switch(self):
        provider = Mock(model="plan-model")
        provider.name = "chatgpt"
        provider.create_response.side_effect = provider_module.plan_error("subscription_sharing_usage_limit_exceeded")
        sent, executed = self.run_messages(["/provider chatgpt", "hello"], provider)
        self.assertEqual(executed, [])
        self.assertIn("Settings → Usage", sent[-1])
        self.assertEqual(provider.create_response.call_count, 1)

    def test_missing_login_returns_to_setup_and_clears_only_test_state(self):
        provider = Mock(model="plan-model")
        provider.name = "chatgpt"
        provider.create_response.side_effect = ChatGPTError("ChatGPT is not signed in. " + bootstrap.LOGIN_HELP)
        sent, executed = self.run_messages(["/provider chatgpt", "hello", "Hi"], provider)
        self.assertEqual(executed, [])
        self.assertEqual(provider.create_response.call_count, 1)
        for choice in ("ChatGPT", "Claude", "OpenAI API", "Anthropic API", "Settings → API keys"):
            self.assertIn(choice, sent[-1])


if __name__ == "__main__":
    unittest.main()
