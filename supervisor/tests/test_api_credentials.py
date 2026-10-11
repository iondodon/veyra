import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_credentials import CredentialStore


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        self.store = CredentialStore(self.state)

    def test_saved_keys_override_environment_and_other_keys_are_preserved(self):
        self.store.save("openai", "saved-openai-key")
        self.store.save("anthropic", "saved-anthropic-key")
        env = CredentialStore(self.state).environment({"OPENAI_API_KEY": "old-key", "PATH": "/bin"})
        self.assertEqual(env, {"OPENAI_API_KEY": "saved-openai-key", "ANTHROPIC_API_KEY": "saved-anthropic-key", "PATH": "/bin"})
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_failed_atomic_replace_preserves_the_previous_key(self):
        self.store.save("openai", "previous-private-key")
        with patch("api_credentials.os.replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                self.store.save("openai", "replacement-private-key")
        self.assertEqual(self.store.environment({})["OPENAI_API_KEY"], "previous-private-key")
        self.assertEqual(list(self.store.directory.glob(".api-keys-*")), [])

    def test_concurrent_writes_preserve_both_services(self):
        errors = []
        def save(provider):
            try:
                CredentialStore(self.state).save(provider, provider + "-private-key")
            except Exception as error:
                errors.append(type(error).__name__)
        workers = [threading.Thread(target=save, args=(provider,)) for provider in ("openai", "anthropic")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(set(json.loads(self.store.path.read_text())), {"openai", "anthropic"})


if __name__ == "__main__":
    unittest.main()
