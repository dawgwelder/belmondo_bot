"""HTTP request identity and rate limiting."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from .webapp_auth import WebAppIdentity


@dataclass(frozen=True)
class RequestIdentity:
    user: WebAppIdentity
    chat_id: int | None


class _RateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._requests: dict[int | str, deque[float]] = defaultdict(deque)

    def allow(self, subject: int | str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        requests = self._requests[subject]
        while requests and requests[0] <= current - 60:
            requests.popleft()
        if len(requests) >= self.limit:
            return False
        requests.append(current)
        return True
