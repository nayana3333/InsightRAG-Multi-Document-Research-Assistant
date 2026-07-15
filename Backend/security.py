import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Thread-safe in-process limiter suitable for a single API instance."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> int:
        """Returns zero when allowed, otherwise the Retry-After value in seconds."""
        now = self.clock()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return max(1, int(window_seconds - (now - events[0]) + 0.999))
            events.append(now)
            if not events:
                self._events.pop(key, None)
        return 0
