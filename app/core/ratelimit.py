"""In-memory sliding-window counters for rate limiting and demo-mode login throttling.

State is per process: with several Cloud Run instances each keeps its own counters,
which is acceptable for a public demo (limits are per instance, not global). A shared
store such as Redis would be needed for strict global limits.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

_MAX_KEYS = 10_000


class SlidingWindow:
    """Counts events per key over the last `window_seconds` seconds."""

    def __init__(self, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.window = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.setdefault(key, deque())
        while events and events[0] <= now - self.window:
            events.popleft()
        return events

    def count(self, key: str) -> int:
        with self._lock:
            return len(self._prune(key, self._clock()))

    def add(self, key: str) -> int:
        """Records an event and returns the number of events in the window."""
        with self._lock:
            now = self._clock()
            if len(self._events) > _MAX_KEYS:
                self._evict(now)
            events = self._prune(key, now)
            events.append(now)
            return len(events)

    def hit(self, key: str, limit: int) -> float | None:
        """Records an event unless the key is over `limit`; returns retry-after seconds."""
        with self._lock:
            now = self._clock()
            if len(self._events) > _MAX_KEYS:
                self._evict(now)
            events = self._prune(key, now)
            if len(events) >= limit:
                return max(events[0] + self.window - now, 0.0)
            events.append(now)
            return None

    def retry_after(self, key: str) -> float:
        with self._lock:
            now = self._clock()
            events = self._prune(key, now)
            return max(events[0] + self.window - now, 0.0) if events else 0.0

    def clear(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)

    def _evict(self, now: float) -> None:
        for key in [k for k, v in self._events.items() if not v or v[-1] <= now - self.window]:
            del self._events[key]
