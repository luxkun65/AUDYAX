"""
Small protection shared by the requests to external services (MusicBrainz, LRCLIB).

If a service is down or very slow, every song would have to wait for its own
timeout. After several connection failures in a row, it isn't queried for a while and
downloads continue without that extra data (a "bonus", not something essential).
"""
import threading
import time


class CircuitBreaker:
    """Stops querying a service after `max_failures` failures in a row, for `pause` seconds."""

    def __init__(self, max_failures: int = 3, pause: float = 300):
        self.max_failures, self.pause = max_failures, pause
        self._failures, self._until = 0, 0.0
        self._lock = threading.Lock()

    def available(self) -> bool:
        with self._lock:
            return time.monotonic() >= self._until

    def success(self):
        with self._lock:
            self._failures = 0

    def failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.max_failures:
                self._until = time.monotonic() + self.pause
                self._failures = 0


class ServiceUnavailable(RuntimeError):
    """Raised instead of querying while the breaker is tripped."""
