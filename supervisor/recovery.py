"""Owner-controlled recovery of committed agent versions."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time

from api_credentials import CredentialStore


class RecoveryError(RuntimeError):
    pass


class Recovery:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / "state/evolution"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.record = self.directory / "last_working.json"
        self.credentials = CredentialStore(self.root / "state")
        self.lock = threading.RLock()
        self.pending = None
        self.busy = False
        self.error = None
        self.diagnostics = []
        self.previews = {}
        try:
            self.last_working = json.loads(self.record.read_text())["revision"]
        except (OSError, ValueError, KeyError, TypeError):
            self.last_working = None

    def git(self, *args, env=None):
        result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                                env=env, timeout=20)
        if result.returncode:
            raise RecoveryError(self.credentials.redact(result.stderr.decode("utf-8", "replace").strip())
                                or "Git could not complete recovery.")
        return result.stdout

    def head(self):
        return self.git("rev-parse", "--verify", "HEAD").decode().strip()

    def changes(self):
        entries = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "agent/").split(b"\0")
        changes = []
        index = 0
        while index < len(entries) and entries[index]:
            entry = entries[index]
            status = entry[:2].decode("ascii")
            changes.append({"path": os.fsdecode(entry[3:]), "status": status})
            index += 2 if "R" in status or "C" in status else 1
        return changes

    def fingerprint(self):
        if (self.root / "agent").is_symlink():
            raise RecoveryError("Recovery requires a real agent directory, not a link.")
        digest = hashlib.sha256(self.head().encode())
        digest.update(self.git("rev-parse", "--symbolic-full-name", "HEAD"))
        for args in (("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "agent/"),
                     ("diff", "--binary", "HEAD", "--", "agent/"),
                     ("diff", "--cached", "--binary", "--", "agent/")):
            digest.update(self.git(*args))
        for name in self.git("ls-files", "--others", "--exclude-standard", "-z", "--", "agent/").split(b"\0"):
            if not name:
                continue
            path = self.root / os.fsdecode(name)
            digest.update(name)
            digest.update(str(path.lstat().st_mode).encode())
            if path.is_symlink():
                digest.update(os.fsencode(os.readlink(path)))
            elif path.is_file():
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(65536), b""):
                        digest.update(chunk)
            else:
                raise RecoveryError("An agent file changed while preparing recovery. Refresh the preview.")
        return digest.hexdigest()

    def publish(self, text, *, kind="status"):
        with self.lock:
            text = self.credentials.redact(str(text))
            self.diagnostics.append(text)
            self.diagnostics = self.diagnostics[-120:]
            if kind == "error":
                self.error = text
            path = self.directory / "supervisor.log"
            with path.open("a", encoding="utf-8") as output:
                os.chmod(path, 0o600)
                output.write(text + "\n")

    def mark_working(self, revision):
        with self.lock:
            if revision == self.last_working:
                return
            descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix=".working-")
            try:
                with os.fdopen(descriptor, "w") as output:
                    json.dump({"revision": revision}, output)
                os.replace(temporary, self.record)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self.last_working = revision

    def status(self, ready, active_revision):
        with self.lock:
            try:
                revision, changes = self.head(), self.changes()
            except (RecoveryError, OSError, subprocess.TimeoutExpired) as exc:
                revision, changes = None, []
                self.error = str(exc)
            return {"ready": ready, "busy": self.busy, "error": self.error,
                    "revision": revision, "active_revision": active_revision,
                    "last_working": self.last_working, "changes": changes,
                    "diagnostics": self.diagnostics[-40:]}

    def preview(self, action):
        with self.lock:
            if self.busy:
                raise RecoveryError("Recovery is already in progress. Please wait.")
            if action not in ("discard", "restore"):
                raise RecoveryError("Choose a recovery action.")
            fingerprint = self.fingerprint()
            changes = self.changes()
            if fingerprint != self.fingerprint():
                raise RecoveryError("Agent files changed while preparing the preview. Try again.")
            if action == "discard" and not changes:
                raise RecoveryError("There are no uncommitted agent changes to discard.")
            if action == "restore":
                if changes:
                    raise RecoveryError("Commit or discard agent changes before restoring a working version.")
                if not self.last_working or self.last_working == self.head():
                    raise RecoveryError("There is no earlier working agent version to restore.")
                self.git("cat-file", "-e", self.last_working + ":agent/START")
            token = secrets.token_urlsafe(24)
            preview = {"action": action, "fingerprint": fingerprint, "expires": time.monotonic() + 120,
                       "target": self.last_working, "paths": [change["path"] for change in changes]}
            self.previews = {key: value for key, value in self.previews.items() if value["expires"] > time.monotonic()}
            if len(self.previews) >= 32:
                self.previews.clear()
            self.previews[token] = preview
            return {"token": token, "action": action, "revision": self.head(),
                    "target": self.last_working, "changes": changes}

    def request(self, payload):
        with self.lock:
            if not isinstance(payload, dict) or payload.get("action") not in ("retry", "discard", "restore"):
                raise RecoveryError("Choose a recovery action.")
            if self.busy:
                raise RecoveryError("Recovery is already in progress. Please wait.")
            action = payload["action"]
            job = {"action": action}
            if action != "retry":
                token = payload.get("token")
                preview = self.previews.pop(token, None) if isinstance(token, str) else None
                if (payload.get("confirmed") is not True or not preview or preview["action"] != action
                        or preview["expires"] <= time.monotonic()):
                    raise RecoveryError("Open a fresh preview and confirm the recovery action.")
                if preview["fingerprint"] != self.fingerprint():
                    raise RecoveryError("Agent files changed since the preview. Review the changes again.")
                job.update(preview)
            self.pending = job
            self.busy = True
            self.error = None

    def take_request(self):
        with self.lock:
            job, self.pending = self.pending, None
            return job

    def execute(self, job):
        """Called by the lifecycle thread after stopping the active agent."""
        if job["action"] == "retry":
            return
        if job["fingerprint"] != self.fingerprint():
            raise RecoveryError("Agent files changed since confirmation. Review the changes again.")
        if job["action"] == "discard":
            self.git("restore", "--source=HEAD", "--staged", "--worktree", "--", "agent/")
            # Clean only individually previewed files. A changed ignore rule must
            # never make previously hidden data part of a confirmed deletion.
            untracked = {os.fsdecode(name) for name in self.git(
                "ls-files", "--others", "--exclude-standard", "-z", "--", "agent/").split(b"\0") if name}
            paths = [path for path in job["paths"] if path in untracked]
            for start in range(0, len(paths), 100):
                self.git("--literal-pathspecs", "clean", "-f", "--", *paths[start:start + 100])
            if self.changes():
                raise RecoveryError("Some agent changes could not be discarded safely. Review the error details.")
            self.publish("Uncommitted agent changes discarded. Retrying startup.")
        else:
            self.restore_version(job["target"])

    def restore_version(self, target):
        """Create a successor commit; preserve branch history and unrelated staged files."""
        head = self.head()
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
            self.git("read-tree", head, env=env)
            self.git("restore", "--source=" + target, "--staged", "--", "agent/", env=env)
            tree = self.git("write-tree", env=env).decode().strip()
            revision = self.git("commit-tree", tree, "-p", head, "-m",
                                "Restore working agent from " + target[:12], env=env).decode().strip()
        self.git("update-ref", "HEAD", revision, head)
        self.git("restore", "--source=HEAD", "--staged", "--worktree", "--", "agent/")
        self.publish("Restored agent version " + target[:12] + " in a new commit. Retrying startup.")
