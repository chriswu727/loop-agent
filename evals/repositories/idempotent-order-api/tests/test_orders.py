import unittest

from orders import create_order


class OrderApiTests(unittest.TestCase):
    def test_create_and_idempotent_replay(self):
        state = {"orders": [], "idempotency": {}}
        first = create_order(state, {"sku": "ABC", "quantity": 2}, "key-1")
        second = create_order(state, {"sku": "ABC", "quantity": 2}, "key-1")
        self.assertEqual(first["status"], 201)
        self.assertEqual(second, first)
        self.assertEqual(len(state["orders"]), 1)

    def test_key_conflict(self):
        state = {"orders": [], "idempotency": {}}
        create_order(state, {"sku": "ABC", "quantity": 1}, "key-1")
        conflict = create_order(state, {"sku": "XYZ", "quantity": 1}, "key-1")
        self.assertEqual(conflict["status"], 409)
        self.assertEqual(len(state["orders"]), 1)

    def test_validation_does_not_mutate_state(self):
        for payload in ({"sku": "", "quantity": 1}, {"sku": "A", "quantity": 0}, {"sku": "A", "quantity": True}):
            state = {"orders": [], "idempotency": {}}
            result = create_order(state, payload, "key")
            self.assertEqual(result["status"], 400)
            self.assertEqual(state, {"orders": [], "idempotency": {}})


if __name__ == "__main__":
    unittest.main()
