import unittest

from rate_limiter import FixedWindowRateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_limit_and_rejection(self):
        limiter = FixedWindowRateLimiter(2, 10)
        self.assertTrue(limiter.allow("a", 1))
        self.assertTrue(limiter.allow("a", 2))
        self.assertFalse(limiter.allow("a", 3))
        self.assertFalse(limiter.allow("a", 9.9))

    def test_keys_are_isolated(self):
        limiter = FixedWindowRateLimiter(1, 5)
        self.assertTrue(limiter.allow("a", 0))
        self.assertTrue(limiter.allow("b", 0))
        self.assertFalse(limiter.allow("a", 1))

    def test_boundary_resets_window(self):
        limiter = FixedWindowRateLimiter(1, 10)
        self.assertTrue(limiter.allow("a", 4))
        self.assertFalse(limiter.allow("a", 13.99))
        self.assertTrue(limiter.allow("a", 14))


if __name__ == "__main__":
    unittest.main()
