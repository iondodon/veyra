import sys
import unittest
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap


def http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(f"{status_code} error", response=response)


class PollingConflictTests(unittest.TestCase):
    """Telegram serves one getUpdates consumer per bot token.

    A second Veyra is answered with 409 Conflict. That failure is not an
    agent error the owner can ignore: it means no message will ever arrive
    until one of the two is stopped.
    """

    def test_conflict_is_recognised(self):
        self.assertTrue(bootstrap.is_polling_conflict(http_error(409)))

    def test_other_http_failures_are_ordinary_errors(self):
        for status in (400, 401, 403, 429, 500):
            with self.subTest(status=status):
                self.assertFalse(bootstrap.is_polling_conflict(http_error(status)))

    def test_non_http_failures_are_ordinary_errors(self):
        self.assertFalse(bootstrap.is_polling_conflict(RuntimeError("boom")))
        self.assertFalse(
            bootstrap.is_polling_conflict(requests.ConnectionError("offline"))
        )

    def test_backoff_leaves_room_for_the_other_poller(self):
        self.assertGreaterEqual(bootstrap.POLLING_CONFLICT_BACKOFF, 1)


if __name__ == "__main__":
    unittest.main()
