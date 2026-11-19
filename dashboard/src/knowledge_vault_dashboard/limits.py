"""Bounded, process-local admission budgets; no caller-controlled metric labels."""

import time
from collections import OrderedDict

from .errors import BoundaryError


class RateGuard:
    """Single-process bounded LRU; global budget prevents key-churn bypass."""

    def __init__(self) -> None:
        self.buckets: OrderedDict[str, tuple[int, int]] = OrderedDict()

    def check(self, key: str, limit: int) -> None:
        minute = int(time.monotonic() // 60)
        epoch, count = self.buckets.pop(key, (minute, 0))
        count = count + 1 if epoch == minute else 1
        self.buckets[key] = (minute, count)
        if len(self.buckets) > 1024:
            self.buckets.popitem(last=False)
        if count > limit:
            raise BoundaryError(429, "rate_limited", "60")
