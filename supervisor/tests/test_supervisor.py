import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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


class SingleInstanceLockTests(unittest.TestCase):
    """One supervisor per repository.

    Two supervisors run two agents against one Telegram bot token, and
    Telegram lets only one of them poll. Refusing the second start keeps
    that failure visible instead of silent.
    """

    def make_root(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def test_lock_records_the_holding_process_under_state(self):
        root = self.make_root()
        descriptor = supervisor.acquire_single_instance_lock(root)
        self.addCleanup(os.close, descriptor)

        lock = root / "state" / supervisor.LOCK_FILE_NAME
        self.assertTrue(lock.is_file())
        self.assertEqual(lock.read_text().strip(), str(os.getpid()))

    def test_second_supervisor_is_refused(self):
        root = self.make_root()
        descriptor = supervisor.acquire_single_instance_lock(root)
        self.addCleanup(os.close, descriptor)

        with self.assertRaisesRegex(
            supervisor.SupervisorError, "another supervisor is already running"
        ):
            supervisor.acquire_single_instance_lock(root)

    def test_refusal_names_the_supervisor_to_stop(self):
        root = self.make_root()
        descriptor = supervisor.acquire_single_instance_lock(root)
        self.addCleanup(os.close, descriptor)

        with self.assertRaisesRegex(
            supervisor.SupervisorError, f"pid {os.getpid()}"
        ):
            supervisor.acquire_single_instance_lock(root)

    def test_lock_is_released_when_the_supervisor_stops(self):
        root = self.make_root()
        os.close(supervisor.acquire_single_instance_lock(root))

        descriptor = supervisor.acquire_single_instance_lock(root)
        self.addCleanup(os.close, descriptor)

    def test_separate_repositories_run_side_by_side(self):
        first = supervisor.acquire_single_instance_lock(self.make_root())
        self.addCleanup(os.close, first)
        second = supervisor.acquire_single_instance_lock(self.make_root())
        self.addCleanup(os.close, second)


class DashboardLifecycleTests(unittest.TestCase):
    def make_root(self, with_dashboard: bool = True) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        if with_dashboard:
            dashboard = root / supervisor.DASHBOARD_DIR_NAME
            dashboard.mkdir()
            (dashboard / "package.json").write_text("{}\n")
        return root

    def test_dashboard_is_optional_for_initial_versions(self):
        root = self.make_root(with_dashboard=False)

        with mock.patch.object(supervisor.subprocess, "Popen") as popen:
            self.assertIsNone(supervisor.start_dashboard(root))

        popen.assert_not_called()

    def test_dashboard_is_started_in_its_own_process_group(self):
        root = self.make_root()
        expected_process = object()

        with (
            mock.patch.object(supervisor.shutil, "which", return_value="/usr/bin/npm"),
            mock.patch.object(
                supervisor.subprocess, "Popen", return_value=expected_process
            ) as popen,
        ):
            process = supervisor.start_dashboard(root)

        self.assertIs(process, expected_process)
        popen.assert_called_once_with(
            ["/usr/bin/npm", "run", "dev"],
            cwd=root / supervisor.DASHBOARD_DIR_NAME,
            start_new_session=True,
        )

    def test_missing_npm_leaves_dashboard_stopped(self):
        root = self.make_root()

        with (
            mock.patch.object(supervisor.shutil, "which", return_value=None),
            mock.patch.object(supervisor.subprocess, "Popen") as popen,
        ):
            self.assertIsNone(supervisor.start_dashboard(root))

        popen.assert_not_called()

    def test_supervisor_stops_dashboard_when_veyra_stops(self):
        root = self.make_root()
        agent = root / "agent"
        agent_process = object()
        dashboard_process = object()

        with (
            mock.patch.object(supervisor, "repository_root", return_value=root),
            mock.patch.object(supervisor, "acquire_single_instance_lock", return_value=17),
            mock.patch.object(
                supervisor,
                "current_revision",
                side_effect=["a" * 40, supervisor.SupervisorError("stopped")],
            ),
            mock.patch.object(supervisor, "prepare_checked_out_agent", return_value=agent),
            mock.patch.object(supervisor, "start_agent", return_value=agent_process),
            mock.patch.object(
                supervisor, "start_dashboard", return_value=dashboard_process
            ),
            mock.patch.object(supervisor, "stop_agent") as stop_agent,
            mock.patch.object(supervisor, "stop_dashboard") as stop_dashboard,
            mock.patch.object(supervisor.signal, "signal"),
            mock.patch.object(supervisor.os, "close"),
        ):
            self.assertEqual(supervisor.main(), 1)

        stop_dashboard.assert_called_once_with(dashboard_process)
        stop_agent.assert_called_once_with(agent_process)


if __name__ == "__main__":
    unittest.main()
