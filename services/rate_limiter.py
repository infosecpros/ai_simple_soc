#!/usr/bin/env python3
"""
Rate Limiter — sliding window.
"""

import time
import logging
from typing import Dict, Tuple, Optional
from collections import deque

from config.settings import get_config

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0):
        self._max = max_requests
        self._window = window_seconds
        self._buckets: Dict[str, deque] = {}

    def consume(self, key: str) -> Tuple[bool, int, float]:
        """(разрешён, использовано всего, секунд до сброса)"""
        now = time.time()
        cutoff = now - self._window

        if key not in self._buckets:
            self._buckets[key] = deque()

        bucket = self._buckets[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        used = len(bucket)
        reset_in = self._window

        if bucket:
            reset_in = max(0.0, self._window - (now - bucket[0]))

        if used >= self._max:
            return False, used, reset_in

        bucket.append(now)
        return True, used + 1, reset_in

    def remaining(self, key: str) -> int:
        # Не consume-им, просто смотрим
        now = time.time()
        cutoff = now - self._window
        if key not in self._buckets:
            return self._max
        bucket = self._buckets[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return self._max - len(bucket)


_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        cfg = get_config().http
        _limiter = RateLimiter(cfg.rate_limit_per_minute)
    return _limiter
