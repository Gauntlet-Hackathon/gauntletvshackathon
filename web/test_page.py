import os
import unittest
from html.parser import HTMLParser

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
TEXT = "Gauntlet vs Hackathon"


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.titles = []
        self.h1s = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        if tag == "title":
            self.titles.append("")
        elif tag == "h1":
            self.h1s.append("")

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if "title" in self.stack:
            self.titles[-1] += data
        elif "h1" in self.stack:
            self.h1s[-1] += data


class LandingPageTest(unittest.TestCase):
    def setUp(self):
        with open(PAGE, encoding="utf-8") as f:
            self.source = f.read()
        self.parsed = Collector()
        self.parsed.feed(self.source)

    def test_doctype(self):
        self.assertTrue(self.source.lstrip().lower().startswith("<!doctype html>"))

    def test_title(self):
        self.assertEqual([t.strip() for t in self.parsed.titles], [TEXT])

    def test_single_h1(self):
        self.assertEqual([h.strip() for h in self.parsed.h1s], [TEXT])


if __name__ == "__main__":
    unittest.main()
