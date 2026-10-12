import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_notes import load_notes, validate_notes


class ReleaseNotesTests(unittest.TestCase):
    def notes(self, path="/dashboard"):
        return {"summary": "Added a dashboard.", "instructions": "Open Dashboard at the top.",
                "links": [{"label": "Open dashboard", "path": path}]}

    def test_versioned_notes_and_relative_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(load_notes(root))
            (root / "agent").mkdir()
            path = root / "agent/release_notes.json"
            path.write_text(json.dumps(self.notes("/dashboard?view=recent#activity")))
            self.assertEqual(load_notes(root), self.notes("/dashboard?view=recent#activity"))

    def test_invalid_metadata_and_external_navigation_are_rejected(self):
        for path in ("https://example.com", "//example.com", "/\\example.com", "/%5cexample.com",
                     "/%2fexample.com", "/\n/example.com", "dashboard", " /dashboard"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_notes(self.notes(path))
        for value in ([], {}, {**self.notes(), "summary": ""}, {**self.notes(), "links": "wrong"},
                      {**self.notes(), "links": [None]}, {**self.notes(), "instructions": "x" * 3001}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_notes(value)
