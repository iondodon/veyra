import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recovery import Recovery, RecoveryError


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git("init", "-qb", "main")
        self.git("config", "user.name", "Veyra Test")
        self.git("config", "user.email", "test@veyra.local")
        self.write(".gitignore", "/state/\n/workspace/\nagent/cache/\n")
        self.write("agent/START", "working version\n")
        self.write("agent/existing.py", "original\n")
        self.write("supervisor/owner.txt", "supervisor\n")
        self.git("add", ".")
        self.git("commit", "-qm", "working")
        self.recovery = Recovery(self.root)
        self.working = self.recovery.head()
        self.recovery.mark_working(self.working)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True).stdout.decode().strip()

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
        return file

    def confirm(self, action):
        preview = self.recovery.preview(action)
        self.recovery.request({"action": action, "token": preview["token"], "confirmed": True})
        return self.recovery.take_request()

    def test_status_polling_does_not_write_the_git_index(self):
        path = self.root / "agent/existing.py"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns - 10_000_000_000))
        index = self.root / ".git/index"
        original = index.read_bytes()
        data = self.recovery.status(True, self.working)
        self.assertEqual(data["changes"], [])
        self.assertEqual(index.read_bytes(), original)

    def test_discard_preserves_state_workspace_ignored_files_and_unrelated_staging(self):
        self.write("agent/START", "draft\n")
        (self.root / "agent/existing.py").unlink()
        self.write("agent/staged.py", "staged\n")
        self.git("add", "agent/staged.py")
        self.write("agent/nested/new file.py", "new\n")
        self.write("agent/cache/private.txt", "ignored agent data\n")
        self.write("state/auth/fixture.json", "private fixture\n")
        self.write("workspace/draft.txt", "work\n")
        self.write("supervisor/owner.txt", "unrelated staged work\n")
        self.git("add", "supervisor/owner.txt")
        before = self.git("diff", "--cached", "--", "supervisor/")
        preview = self.recovery.preview("discard")
        self.assertEqual(len(preview["changes"]), 4)
        self.assertEqual((self.root / "agent/START").read_text(), "draft\n")
        job = self.confirm("discard")
        self.recovery.execute(job)
        self.assertEqual(self.recovery.changes(), [])
        self.assertEqual((self.root / "agent/START").read_text(), "working version\n")
        self.assertTrue((self.root / "agent/existing.py").exists())
        self.assertFalse((self.root / "agent/staged.py").exists())
        self.assertFalse((self.root / "agent/nested/new file.py").exists())
        self.assertEqual((self.root / "agent/cache/private.txt").read_text(), "ignored agent data\n")
        self.assertEqual((self.root / "state/auth/fixture.json").read_text(), "private fixture\n")
        self.assertEqual((self.root / "workspace/draft.txt").read_text(), "work\n")
        self.assertEqual(self.git("diff", "--cached", "--", "supervisor/"), before)
        self.assertEqual(self.recovery.head(), self.working)

    def test_stale_preview_detects_tracked_and_untracked_content_edits(self):
        for name in ("agent/START", "agent/new.py"):
            with self.subTest(path=name):
                self.write(name, "draft one\n")
                preview = self.recovery.preview("discard")
                self.write(name, "draft two\n")
                with self.assertRaisesRegex(RecoveryError, "changed since"):
                    self.recovery.request({"action": "discard", "token": preview["token"], "confirmed": True})
                self.assertEqual((self.root / name).read_text(), "draft two\n")

    def test_execution_rechecks_after_confirmation(self):
        self.write("agent/START", "draft one\n")
        job = self.confirm("discard")
        self.write("agent/START", "draft two\n")
        with self.assertRaisesRegex(RecoveryError, "changed since"):
            self.recovery.execute(job)
        self.assertEqual((self.root / "agent/START").read_text(), "draft two\n")

    def test_confirmation_is_required_and_cannot_be_replayed(self):
        self.write("agent/START", "draft\n")
        preview = self.recovery.preview("discard")
        with self.assertRaisesRegex(RecoveryError, "confirm"):
            self.recovery.request({"action": "discard", "token": preview["token"]})
        preview = self.recovery.preview("discard")
        payload = {"action": "discard", "token": preview["token"], "confirmed": True}
        self.recovery.request(payload)
        self.recovery.take_request()
        self.recovery.busy = False
        with self.assertRaisesRegex(RecoveryError, "fresh preview"):
            self.recovery.request(payload)

    def test_head_change_invalidates_confirmation(self):
        self.write("agent/START", "draft\n")
        preview = self.recovery.preview("discard")
        self.git("commit", "--allow-empty", "-qm", "new head")
        with self.assertRaisesRegex(RecoveryError, "changed since"):
            self.recovery.request({"action": "discard", "token": preview["token"], "confirmed": True})

    def test_restore_creates_successor_on_main_and_preserves_other_staged_files(self):
        self.write("agent/START", "broken version\n")
        self.write("agent/broken.py", "broken\n")
        self.git("add", "agent/")
        self.git("commit", "-qm", "broken")
        broken = self.recovery.head()
        self.write("supervisor/owner.txt", "pending\n")
        self.git("add", "supervisor/")
        before = self.git("diff", "--cached", "--", "supervisor/")
        self.recovery.execute(self.confirm("restore"))
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD^"), broken)
        self.assertEqual(self.git("diff", "--cached", "--", "supervisor/"), before)
        self.assertEqual((self.root / "agent/START").read_text(), "working version\n")
        self.assertFalse((self.root / "agent/broken.py").exists())
        self.assertEqual(self.recovery.changes(), [])

    def test_restore_refuses_dirty_agent_and_no_earlier_version(self):
        with self.assertRaisesRegex(RecoveryError, "no earlier"):
            self.recovery.preview("restore")
        self.write("agent/START", "draft\n")
        with self.assertRaisesRegex(RecoveryError, "Commit or discard"):
            self.recovery.preview("restore")

    def test_rename_and_unusual_filenames_are_previewed_and_discarded(self):
        self.git("mv", "agent/existing.py", "agent/renamed\nfile.py")
        self.write("agent/untracked\nfile.py", "new\n")
        self.assertEqual({change["path"] for change in self.recovery.changes()},
                         {"agent/renamed\nfile.py", "agent/untracked\nfile.py"})
        self.recovery.execute(self.confirm("discard"))
        self.assertEqual(self.recovery.changes(), [])

    def test_last_working_version_survives_restart(self):
        self.assertEqual(Recovery(self.root).last_working, self.working)

    def test_branch_change_at_same_commit_invalidates_confirmation(self):
        self.write("agent/START", "draft\n")
        preview = self.recovery.preview("discard")
        self.git("switch", "-qc", "experiment")
        with self.assertRaisesRegex(RecoveryError, "changed since"):
            self.recovery.request({"action": "discard", "token": preview["token"], "confirmed": True})

    def test_changed_ignore_rules_cannot_expand_approved_deletions_or_glob_paths(self):
        original = (self.root / ".gitignore").read_text()
        self.write(".gitignore", original + "agent/x.py\n")
        self.write("agent/[x].py", "approved draft\n")
        self.write("agent/x.py", "protected data\n")
        job = self.confirm("discard")
        real_git = self.recovery.git
        def changing_git(*args, env=None):
            result = real_git(*args, env=env)
            if args[0] == "restore":
                self.write(".gitignore", original)
            return result
        with patch.object(self.recovery, "git", side_effect=changing_git):
            with self.assertRaisesRegex(RecoveryError, "could not be discarded safely"):
                self.recovery.execute(job)
        self.assertFalse((self.root / "agent/[x].py").exists())
        self.assertEqual((self.root / "agent/x.py").read_text(), "protected data\n")

    def test_preview_expires_and_wrong_action_cannot_use_token(self):
        self.write("agent/START", "draft\n")
        preview = self.recovery.preview("discard")
        self.recovery.previews[preview["token"]]["expires"] = 0
        with self.assertRaisesRegex(RecoveryError, "fresh preview"):
            self.recovery.request({"action": "discard", "token": preview["token"], "confirmed": True})
        preview = self.recovery.preview("discard")
        with self.assertRaisesRegex(RecoveryError, "fresh preview"):
            self.recovery.request({"action": "restore", "token": preview["token"], "confirmed": True})
