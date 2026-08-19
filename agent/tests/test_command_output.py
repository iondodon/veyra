import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap


class CommandOutputTests(unittest.TestCase):
    def test_non_utf8_stdout_and_stderr_do_not_crash(self):
        program = (
            "import sys; "
            "sys.stdout.buffer.write(b'before\\x85after'); "
            "sys.stderr.buffer.write(b'bad\\xffdata')"
        )
        result = bootstrap.run_local(
            f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"
        )
        self.assertEqual(result["outcome"]["exit_code"], 0)
        self.assertEqual(result["stdout"], r"before\x85after")
        self.assertEqual(result["stderr"], r"bad\xffdata")


if __name__ == "__main__":
    unittest.main()
