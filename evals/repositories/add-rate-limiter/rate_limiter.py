class FixedWindowRateLimiter:
    def __init__(self, limit, window_seconds):
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("limit and window_seconds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds

    def allow(self, key, now):
        raise NotImplementedError
