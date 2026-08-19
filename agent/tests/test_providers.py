import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
import providers


class ProviderSelectionTests(unittest.TestCase):
    def test_choice_parsing_is_forgiving_about_case_and_spacing(self):
        self.assertEqual(providers.parse_provider_choice("openai"), "openai")
        self.assertEqual(providers.parse_provider_choice(" Anthropic "), "anthropic")

    def test_only_known_providers_parse(self):
        for junk in ("gemini", "", None, 7, "openai anthropic"):
            self.assertIsNone(providers.parse_provider_choice(junk))

    def test_no_selection_file_means_no_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                providers.load_selected_provider(Path(tmp) / "provider.json")
            )

    def test_selection_survives_a_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "provider.json"
            providers.save_selected_provider(path, "anthropic")
            self.assertEqual(providers.load_selected_provider(path), "anthropic")
            providers.save_selected_provider(path, "openai")
            self.assertEqual(providers.load_selected_provider(path), "openai")

    def test_corrupted_selection_state_means_no_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "provider.json"
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(providers.load_selected_provider(path))
            path.write_text('{"provider": "gemini"}', encoding="utf-8")
            self.assertIsNone(providers.load_selected_provider(path))

    def test_unknown_selection_is_never_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                providers.save_selected_provider(
                    Path(tmp) / "provider.json", "gemini"
                )

    def test_available_keys_do_not_imply_a_choice(self):
        # Even with every key present, a provider must be named explicitly.
        env = {"OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k"}
        for missing_choice in (None, ""):
            with self.assertRaises(providers.ProviderConfigError):
                providers.create_provider(missing_choice, env)

    def test_selected_provider_requires_its_key(self):
        with self.assertRaises(providers.ProviderConfigError):
            providers.create_provider("anthropic", {"OPENAI_API_KEY": "k"})
        with self.assertRaises(providers.ProviderConfigError):
            providers.create_provider("openai", {"ANTHROPIC_API_KEY": "k"})

    def test_selected_provider_builds_with_its_key(self):
        openai_provider = providers.create_provider(
            "openai", {"OPENAI_API_KEY": "k"}
        )
        self.assertEqual(openai_provider.name, "openai")
        self.assertEqual(openai_provider.model, "gpt-5.6")
        anthropic_provider = providers.create_provider(
            "anthropic", {"ANTHROPIC_API_KEY": "k"}
        )
        self.assertEqual(anthropic_provider.name, "anthropic")
        self.assertEqual(anthropic_provider.model, "claude-opus-5")


class ProviderWiringTests(unittest.TestCase):
    def test_selection_is_stored_in_persistent_state(self):
        self.assertEqual(
            bootstrap.PROVIDER_FILE,
            bootstrap.STATE / "memory" / "provider.json",
        )

    def test_intro_explains_explicit_provider_selection(self):
        self.assertIn("/provider openai", bootstrap.INTRO)
        self.assertIn("/provider anthropic", bootstrap.INTRO)

    def test_provider_command_persists_the_choice(self):
        source = Path(bootstrap.__file__).read_text(encoding="utf-8")
        self.assertIn('text.startswith("/provider ")', source)
        self.assertIn("save_selected_provider(PROVIDER_FILE, choice)", source)


class AnthropicAdapterTests(unittest.TestCase):
    def test_shell_tool_becomes_strict_commands_tool(self):
        translated = providers.translate_tools(bootstrap.SHELL_TOOLS)
        self.assertEqual(len(translated), 1)
        shell = translated[0]
        self.assertEqual(shell["name"], "shell")
        self.assertTrue(shell["strict"])
        self.assertEqual(shell["input_schema"]["required"], ["commands"])

    def test_chat_text_becomes_a_user_turn(self):
        self.assertEqual(
            providers.user_turn("hello"),
            {"role": "user", "content": "hello"},
        )

    def test_shell_outputs_become_tool_results(self):
        turn = providers.user_turn([
            {"type": "shell_call_output", "call_id": "c1",
             "output": [{"stdout": "ok"}]},
        ])
        self.assertEqual(turn["role"], "user")
        self.assertEqual(turn["content"][0]["tool_use_id"], "c1")
        self.assertEqual(
            json.loads(turn["content"][0]["content"]), [{"stdout": "ok"}]
        )

    def test_tool_use_normalizes_to_the_agent_shell_shape(self):
        blocks = [
            SimpleNamespace(type="text", text="Working"),
            SimpleNamespace(
                type="tool_use", id="tu-1", name="shell",
                input={"commands": ["ls", "pwd"]},
            ),
        ]
        text, calls = providers.normalize_content(blocks)
        self.assertEqual(text, "Working")
        self.assertEqual(calls[0].type, "shell_call")
        self.assertEqual(calls[0].call_id, "tu-1")
        self.assertEqual(calls[0].action.commands, ["ls", "pwd"])

    def test_thinking_blocks_carry_no_chat_output(self):
        blocks = [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="Done"),
        ]
        text, calls = providers.normalize_content(blocks)
        self.assertEqual(text, "Done")
        self.assertEqual(calls, [])

    def test_conversation_round_trip_without_network(self):
        provider = providers.AnthropicProvider(
            api_key="test-key", model="claude-opus-5"
        )
        turn1 = SimpleNamespace(
            id="msg_1", stop_reason="tool_use",
            content=[SimpleNamespace(
                type="tool_use", id="tu1", name="shell",
                input={"commands": ["ls"]},
            )],
        )
        turn2 = SimpleNamespace(
            id="msg_2", stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="done")],
        )
        requests_seen = []

        def fake_call(request):
            requests_seen.append({
                key: (list(value) if key == "messages" else value)
                for key, value in request.items()
            })
            return turn1 if len(requests_seen) == 1 else turn2

        with patch.object(provider, "_call", side_effect=fake_call):
            first = provider.create_response(
                instructions="sys prompt",
                input="list files",
                tools=bootstrap.SHELL_TOOLS,
            )
            self.assertEqual(first.output[0].action.commands, ["ls"])

            second = provider.create_response(
                instructions="sys prompt",
                previous_response_id=first.id,
                input=[{"type": "shell_call_output", "call_id": "tu1",
                        "output": [{"stdout": "a b c"}]}],
                tools=bootstrap.SHELL_TOOLS,
            )
            self.assertEqual(second.output_text, "done")
            self.assertEqual(second.output, [])

        first_request, second_request = requests_seen
        self.assertEqual(first_request["system"], "sys prompt")
        self.assertEqual(
            first_request["betas"], ["server-side-fallback-2026-07-01"]
        )
        self.assertEqual(first_request["fallbacks"], "default")
        # The continuation replays the full history: the user turn, the
        # assistant tool use, and the shell results as one tool_result turn.
        roles = [m["role"] for m in second_request["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertEqual(
            second_request["messages"][2]["content"][0]["tool_use_id"], "tu1"
        )


if __name__ == "__main__":
    unittest.main()
