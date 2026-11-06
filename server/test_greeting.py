import unittest

from greeting import greet


class GreetTest(unittest.TestCase):
    def test_greets_by_name(self):
        self.assertEqual(greet("Ada"), "Hello, Ada!")

    def test_empty_name_raises(self):
        with self.assertRaises(ValueError):
            greet("")

    def test_whitespace_name_raises(self):
        with self.assertRaises(ValueError):
            greet("   ")


if __name__ == "__main__":
    unittest.main()
