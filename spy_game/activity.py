"""Cheap in-memory activity aggregation; economy never lives here."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta


class ActivityTracker:
    def __init__(self, user_debounce_seconds: int) -> None:
        self._debounce = timedelta(seconds=user_debounce_seconds)
        self._counts: dict[int, int] = defaultdict(int)
        self._last_user_activity: dict[tuple[int, int], datetime] = {}
        self._lock = asyncio.Lock()

    async def record(self, chat_id: int, user_id: int, now: datetime) -> bool:
        key = (chat_id, user_id)
        async with self._lock:
            previous = self._last_user_activity.get(key)
            if previous is not None and now - previous < self._debounce:
                return False
            self._last_user_activity[key] = now
            self._counts[chat_id] += 1
            return True

    async def drain(self) -> dict[int, int]:
        async with self._lock:
            drained = dict(self._counts)
            self._counts.clear()
            return drained

    async def restore(self, counts: dict[int, int]) -> None:
        async with self._lock:
            for chat_id, count in counts.items():
                self._counts[chat_id] += count
