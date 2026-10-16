import time
from typing import Set

class IdempotencyRegistry:
    def __init__(self, ttl_seconds: int = 300):
        self._processed: Set[str] = set()
        self._timestamps: dict[str, float] = {}
        self.ttl = ttl_seconds

    def is_processed(self, key: str) -> bool:
        self._clean()
        return key in self._processed

    def mark_processed(self, key: str) -> bool:
        self._clean()
        if key in self._processed:
            return False
        self._processed.add(key)
        self._timestamps[key] = time.time()
        return True

    def _clean(self):
        now = time.time()
        expired = [k for k, t in self._timestamps.items() if now - t > self.ttl]
        for k in expired:
            self._processed.discard(k)
            self._timestamps.pop(k, None)

idempotency = IdempotencyRegistry()
