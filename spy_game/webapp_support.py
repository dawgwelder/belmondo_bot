"""HTTP request identity and rate limiting."""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from .models import ChatMembership
from .webapp_auth import WebAppIdentity


@dataclass(frozen=True)
class RequestIdentity:
    user: WebAppIdentity
    chat_id: int | None
    chats: tuple[ChatMembership, ...] = ()


class _RateLimiter:
    def __init__(self, limit: int, *, max_subjects: int = 1024) -> None:
        self.limit = limit
        self.max_subjects = max_subjects
        self._requests: OrderedDict[int | str, deque[float]] = OrderedDict()

    def allow(self, subject: int | str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        while self._requests:
            oldest = next(iter(self._requests.values()))
            if oldest[-1] > current - 60:
                break
            self._requests.popitem(last=False)
        requests = self._requests.get(subject)
        if requests is None:
            if len(self._requests) >= self.max_subjects:
                return False
            requests = self._requests[subject] = deque()
        while requests and requests[0] <= current - 60:
            requests.popleft()
        if len(requests) >= self.limit:
            return False
        requests.append(current)
        self._requests.move_to_end(subject)
        return True
