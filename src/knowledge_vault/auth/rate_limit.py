import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimit:
    limit: int
    window_seconds: float = 60.0


class SlidingWindowRateLimiter:
    """Bounded in-process limiter suitable for the chart's single API replica default."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(
        self, principal_id: str, operation_class: str, policy: RateLimit, now: float | None = None
    ) -> bool:
        current = time.monotonic() if now is None else now
        cutoff = current - policy.window_seconds
        events = self._events[(principal_id, operation_class)]
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= policy.limit:
            return False
        events.append(current)
        return True
