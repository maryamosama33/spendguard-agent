import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Sliding-window limiter: at most max_calls per window seconds.

    acquire() blocks until a slot is free, so callers wait instead of
    hitting the provider's quota and failing mid-conversation.
    """

    def __init__(
        self,
        max_calls: int,
        window: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.max_calls = max_calls
        self.window = window
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def _drop_expired(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self.window:
            self._calls.popleft()

    def _wait_time(self) -> float:
        """Seconds until a slot frees up (0 if one is free now)."""
        now = self._clock()
        self._drop_expired(now)
        if len(self._calls) < self.max_calls:
            return 0.0
        return self.window - (now - self._calls[0])

    def acquire(self) -> float:
        """Block until a call is allowed, record it, and return seconds waited."""
        with self._lock:
            waited = 0.0
            while (delay := self._wait_time()) > 0:
                self._sleep(delay)
                waited += delay
            self._calls.append(self._clock())
            return waited
