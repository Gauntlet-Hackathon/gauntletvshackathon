import os
import subprocess
import sys
import unittest

from greeting import farewell, greet


class GreetTest(unittest.TestCase):
    def test_greets_by_name(self):
        self.assertEqual(greet("Ada"), "Hello, Ada!")

    def test_empty_name_raises(self):
        with self.assertRaises(ValueError):
            greet("")

    def test_whitespace_name_raises(self):
        with self.assertRaises(ValueError):
            greet("   ")


class FarewellTest(unittest.TestCase):
    def test_says_goodbye(self):
        self.assertEqual(farewell("Ada"), "Goodbye, Ada!")

    def test_empty_name_raises(self):
        with self.assertRaises(ValueError):
            farewell("")

    def test_whitespace_name_raises(self):
        with self.assertRaises(ValueError):
            farewell("   ")


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run(
            [sys.executable, "-m", "server.cli", *args],
            cwd=root, capture_output=True, text=True,
        )

    def test_prints_greeting_then_farewell(self):
        result = self.run_cli("Ada")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "Hello, Ada!\nGoodbye, Ada!\n")

    def test_empty_name_fails(self):
        self.assertEqual(self.run_cli("").returncode, 1)


if __name__ == "__main__":
    unittest.main()
