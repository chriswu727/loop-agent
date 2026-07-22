import unittest

from pricing import calculate_total


class PricingTests(unittest.TestCase):
    def test_quantity_and_line_discount(self):
        lines = [
            {"price": 12.50, "quantity": 2, "discount_pct": 20},
            {"price": 3.00, "quantity": 3},
        ]
        self.assertEqual(calculate_total(lines), 29.0)

    def test_empty_cart(self):
        self.assertEqual(calculate_total([]), 0.0)

    def test_negative_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            calculate_total([{"price": 1, "quantity": -1}])


if __name__ == "__main__":
    unittest.main()
