import unittest

from config import load_config


class ConfigTests(unittest.TestCase):
    def test_boolean_spellings(self):
        true_values = ("true", "TRUE", "1", "yes", "on")
        false_values = ("false", "FALSE", "0", "no", "off")
        for value in true_values:
            self.assertIs(load_config({"FEATURE__ENABLED": value})["feature"]["enabled"], True)
        for value in false_values:
            self.assertIs(load_config({"FEATURE__ENABLED": value})["feature"]["enabled"], False)

    def test_unknown_boolean_is_rejected(self):
        with self.assertRaises(ValueError):
            load_config({"FEATURE__ENABLED": "perhaps"})

    def test_nested_integer_and_input_immutability(self):
        values = {"SERVER__PORT": "8080", "SERVER__HOST": "localhost"}
        original = dict(values)
        self.assertEqual(load_config(values), {"server": {"port": 8080, "host": "localhost"}})
        self.assertEqual(values, original)


if __name__ == "__main__":
    unittest.main()
