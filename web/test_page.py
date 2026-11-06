import os
import unittest
from html.parser import HTMLParser

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
TEXT = "Gauntlet vs Hackathon"


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.doctype = None
        self.titles = []
        self.h1s = []
        self._current = None

    def handle_decl(self, decl):
        self.doctype = decl

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.titles.append("")
            self._current = self.titles
        elif tag == "h1":
            self.h1s.append("")
            self._current = self.h1s

    def handle_endtag(self, tag):
        if tag in ("title", "h1"):
            self._current = None

    def handle_data(self, data):
        if self._current is not None:
            self._current[-1] += data


class LandingPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PAGE, encoding="utf-8") as f:
            cls.parsed = _PageParser()
            cls.parsed.feed(f.read())
            cls.parsed.close()

    def test_html5_doctype(self):
        self.assertEqual((self.parsed.doctype or "").lower(), "doctype html")

    def test_title(self):
        self.assertEqual([t.strip() for t in self.parsed.titles], [TEXT])

    def test_single_h1(self):
        self.assertEqual([h.strip() for h in self.parsed.h1s], [TEXT])


if __name__ == "__main__":
    unittest.main()
