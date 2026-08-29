import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from memory import DEFAULT_MESSAGE_LIMIT, RecentConversation


class RecentConversationTests(unittest.TestCase):
    def test_missing_and_corrupt_memory_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recent.json"
            self.assertEqual(RecentConversation(path).messages, [])
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(RecentConversation(path).messages, [])

    def test_messages_survive_reload_and_only_last_twenty_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recent.json"
            memory = RecentConversation(path)
            for number in range(25):
                role = "user" if number % 2 == 0 else "assistant"
                memory.append(role, f"message {number}", f"commit-{number}")

            restored = RecentConversation(path)
            self.assertEqual(len(restored.messages), DEFAULT_MESSAGE_LIMIT)
            self.assertEqual(restored.messages[0]["content"], "message 5")
            self.assertEqual(restored.messages[-1]["content"], "message 24")
            self.assertEqual(restored.messages[-1]["commit"], "commit-24")
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_invalid_records_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recent.json"
            path.write_text(json.dumps({"messages": [
                {"role": "user", "content": "valid"},
                {"role": "system", "content": "invalid role"},
                {"role": "assistant", "content": 42},
                "invalid shape",
            ]}), encoding="utf-8")
            self.assertEqual(
                RecentConversation(path).messages,
                [{"role": "user", "content": "valid"}],
            )

    def test_memory_is_added_only_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = RecentConversation(Path(tmp) / "recent.json")
            self.assertEqual(memory.add_to_instructions("base"), "base")
            memory.append("user", "Remember this", "old-commit")
            rendered = memory.add_to_instructions("base")
            self.assertIn("# Recent conversation memory", rendered)
            self.assertIn("Remember this", rendered)
            self.assertIn("old-commit", rendered)

    def test_returned_messages_cannot_mutate_stored_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = RecentConversation(Path(tmp) / "recent.json")
            memory.append("user", "original")
            copy = memory.messages
            copy[0]["content"] = "changed"
            self.assertEqual(memory.messages[0]["content"], "original")


class MemoryWiringTests(unittest.TestCase):
    def test_memory_file_is_in_persistent_state(self):
        self.assertEqual(
            bootstrap.CONVERSATION_FILE,
            bootstrap.STATE / "memory" / "recent_messages.json",
        )

    def test_intro_documents_memory_window(self):
        self.assertIn("last 20", bootstrap.INTRO)
        self.assertIn("version changes", bootstrap.INTRO)


class OperationalKnowledgeTests(unittest.TestCase):
    def test_versioned_operational_notes_are_always_in_instructions(self):
        rendered = bootstrap.load_instructions()
        self.assertIn("# Versioned operational memory", rendered)
        self.assertIn("Selecting a tab in Ghostty", rendered)
        self.assertIn("wtype -s 150 -M alt -k 2 -m alt -s 300", rendered)

    def test_operational_notes_are_agent_code_not_ephemeral_workspace(self):
        self.assertEqual(
            bootstrap.OPERATIONAL_NOTES,
            bootstrap.AGENT_DIR / "operational_notes.md",
        )


if __name__ == "__main__":
    unittest.main()
