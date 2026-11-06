import unittest

from greeting import greet


class GreetTest(unittest.TestCase):
    def test_greets(self):
        self.assertEqual(greet("Ada"), "Hello, Ada!")

    def test_empty(self):
        with self.assertRaises(ValueError):
            greet("")

    def test_whitespace(self):
        with self.assertRaises(ValueError):
            greet("   ")


if __name__ == "__main__":
    unittest.main()
