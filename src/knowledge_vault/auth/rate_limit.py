import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from knowledge_vault.auth.tokens import Scope
from knowledge_vault.config import Settings
from knowledge_vault.services.errors import RateLimitedError


@dataclass(frozen=True, slots=True)
class RateLimit:
    limit: int
    window_seconds: float = 60.0


class SlidingWindowRateLimiter:
    """Bounded in-process limiter suitable for the chart's single API replica default."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def retry_after(
        self, principal_id: str, operation_class: str, policy: RateLimit, now: float | None = None
    ) -> float | None:
        """Record one event and return None, or return the seconds until a slot frees up."""
        current = time.monotonic() if now is None else now
        cutoff = current - policy.window_seconds
        events = self._events[(principal_id, operation_class)]
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= policy.limit:
            return events[0] + policy.window_seconds - current
        events.append(current)
        return None

    def allow(
        self, principal_id: str, operation_class: str, policy: RateLimit, now: float | None = None
    ) -> bool:
        return self.retry_after(principal_id, operation_class, policy, now) is None


_OPERATION_CLASS = {Scope.READ: "read", Scope.WRITE: "write", Scope.ADMIN: "admin"}


class RateLimitGuard:
    """Per-principal, per-operation-class limits shared by the MCP and HTTP adapters."""

    def __init__(self, settings: Settings) -> None:
        self._limiter = SlidingWindowRateLimiter()
        self._limits = {
            Scope.READ: settings.rate_read_per_minute,
            Scope.WRITE: settings.rate_write_per_minute,
            Scope.ADMIN: settings.rate_admin_per_minute,
        }

    def enforce(self, principal_id: str, scope: Scope) -> None:
        operation_class = _OPERATION_CLASS[scope]
        wait = self._limiter.retry_after(
            principal_id, operation_class, RateLimit(self._limits[scope])
        )
        if wait is not None:
            raise RateLimitedError(
                f"{operation_class} rate limit exceeded", retry_after_seconds=math.ceil(wait)
            )
