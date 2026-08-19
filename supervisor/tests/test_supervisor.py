import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SUPERVISOR_PATH = Path(__file__).resolve().parents[1] / "supervisor"
loader = importlib.machinery.SourceFileLoader("veyra_supervisor", str(SUPERVISOR_PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
supervisor = importlib.util.module_from_spec(spec)
loader.exec_module(supervisor)


class GitAgentTests(unittest.TestCase):
    def make_repository(
        self,
        self_test_exit_code: int = 0,
        branch: str = "main",
    ) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        agent = root / "agent"
        agent.mkdir()
        start = agent / "START"
        start.write_text(
            f"#!/bin/sh\nif [ \"${{1:-}}\" = --self-test ]; then "
            f"exit {self_test_exit_code}; fi\nexit 0\n"
        )
        start.chmod(0o755)

        subprocess.run(
            ["git", "init", "-q", "-b", branch, str(root)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "config", "user.name", "Veyra Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "config", "user.email", "test@veyra.local"],
            check=True,
        )
        subprocess.run(["git", "-C", str(root), "add", "agent"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "commit", "-q", "-m", "agent"],
            check=True,
        )
        return root

    def test_current_revision_is_git_head(self):
        root = self.make_repository()
        expected = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        self.assertEqual(supervisor.current_revision(root), expected)

    def test_main_branch_agent_can_be_prepared(self):
        root = self.make_repository(branch="main")
        revision = supervisor.current_revision(root)

        self.assertEqual(
            supervisor.prepare_checked_out_agent(root, revision),
            root / "agent",
        )

    def test_committed_agent_is_accepted(self):
        root = self.make_repository()

        supervisor.require_committed_agent(root)
        self.assertEqual(supervisor.validate_agent(root), root / "agent")

    def test_dirty_agent_is_rejected(self):
        root = self.make_repository()
        (root / "agent" / "bootstrap.py").write_text("print('draft')\n")

        with self.assertRaisesRegex(supervisor.SupervisorError, "uncommitted"):
            supervisor.require_committed_agent(root)

    def test_untracked_start_is_rejected(self):
        root = self.make_repository()
        subprocess.run(
            ["git", "-C", str(root), "rm", "-q", "--cached", "agent/START"],
            check=True,
        )

        with self.assertRaisesRegex(supervisor.SupervisorError, "not tracked"):
            supervisor.require_committed_agent(root)

    def test_self_test_controls_activation(self):
        healthy = self.make_repository()
        broken = self.make_repository(self_test_exit_code=7)

        self.assertTrue(
            supervisor.run_self_test(
                healthy / "agent", supervisor.current_revision(healthy)
            )
        )
        self.assertFalse(
            supervisor.run_self_test(
                broken / "agent", supervisor.current_revision(broken)
            )
        )

    def test_agent_directory_cannot_be_a_symlink(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        target = root / "elsewhere"
        target.mkdir()
        (target / "START").write_text("#!/bin/sh\n")
        os.chmod(target / "START", 0o755)
        (root / "agent").symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(supervisor.SupervisorError, "real directory"):
            supervisor.validate_agent(root)


if __name__ == "__main__":
    unittest.main()
