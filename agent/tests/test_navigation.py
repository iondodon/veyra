"""Keep application navigation independent of host-local recovery."""
from html.parser import HTMLParser
from pathlib import Path
import unittest


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.targets.extend(value for name, value in attrs if name == "href")


class NavigationTests(unittest.TestCase):
    def test_pages_do_not_link_to_removed_recovery_route(self):
        for page in Path(__file__).resolve().parents[1].glob("*.html"):
            with self.subTest(page=page.name):
                links = Links()
                links.feed(page.read_text())
                for target in links.targets:
                    self.assertFalse((target or "").split("?", 1)[0].split("#", 1)[0].rstrip("/") == "/recovery", target)


if __name__ == "__main__":
    unittest.main()
