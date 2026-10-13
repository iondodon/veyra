"""Private, durable API keys shared by the web page and agent."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile


KEY_NAMES = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
ASSIGNMENT_PATTERN = re.compile(r"\b(?:OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*[:=]\s*\S+", re.I)


class CredentialError(RuntimeError):
    pass


class CredentialStore:
    def __init__(self, state):
        self.directory = Path(state) / "auth"
        self.path = self.directory / "api_keys.json"

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        descriptor = os.open(self.directory / "api_keys.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _read(self):
        try:
            self.path.chmod(0o600)
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise CredentialError("Veyra couldn't read the saved API keys. Check the private credential file.") from None
        if not isinstance(data, dict) or any(name not in KEY_NAMES or not isinstance(key, str)
                                             for name, key in data.items()):
            raise CredentialError("The saved API keys have an invalid format.")
        return data

    def environment(self, env=None):
        values = dict(os.environ if env is None else env)
        with self.locked():
            saved = self._read()
        values.update({KEY_NAMES[name]: key for name, key in saved.items()})
        return values

    def save(self, provider, key):
        if not isinstance(provider, str) or provider not in KEY_NAMES:
            raise CredentialError("Choose OpenAI or Anthropic.")
        if not isinstance(key, str) or not re.fullmatch(r"[!-~]{8,4096}", key):
            raise CredentialError("Enter an API key without spaces or line breaks.")
        with self.locked():
            data = self._read()
            data[provider] = key
            descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix=".api-keys-")
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(data, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
                directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def secrets(self):
        try:
            env = self.environment()
        except (CredentialError, OSError):
            env = os.environ
        return list({source[name] for source in (os.environ, env)
                     for name in KEY_NAMES.values() if source.get(name)})

    def contains_secret(self, text):
        return bool(KEY_PATTERN.search(text) or ASSIGNMENT_PATTERN.search(text)
                    or any(key in text for key in self.secrets()))

    def redact(self, text):
        for key in sorted(self.secrets(), key=len, reverse=True):
            text = text.replace(key, "[API key hidden]")
        text = KEY_PATTERN.sub("[API key hidden]", text)
        return ASSIGNMENT_PATTERN.sub("[API key hidden]", text)
