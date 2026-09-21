"""Cheap in-memory activity aggregation; economy never lives here."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class MemberTouch:
    """Latest contact between a user and a group chat, flushed by the tick."""

    last_seen_at: datetime
    title: str | None


class ActivityTracker:
    def __init__(self, user_debounce_seconds: int) -> None:
        self._debounce = timedelta(seconds=user_debounce_seconds)
        self._counts: dict[int, int] = defaultdict(int)
        self._last_user_activity: dict[tuple[int, int], datetime] = {}
        self._touches: dict[tuple[int, int], MemberTouch] = {}
        self._lock = asyncio.Lock()

    def _touch_locked(
        self,
        chat_id: int,
        user_id: int,
        now: datetime,
        title: str | None,
    ) -> None:
        key = (chat_id, user_id)
        previous = self._touches.get(key)
        if previous is not None and previous.last_seen_at > now:
            now = previous.last_seen_at
        if title is None and previous is not None:
            title = previous.title
        self._touches[key] = MemberTouch(now, title)

    async def record(
        self,
        chat_id: int,
        user_id: int,
        now: datetime,
        title: str | None = None,
    ) -> bool:
        key = (chat_id, user_id)
        async with self._lock:
            self._touch_locked(chat_id, user_id, now, title)
            previous = self._last_user_activity.get(key)
            if previous is not None and now - previous < self._debounce:
                return False
            self._last_user_activity[key] = now
            self._counts[chat_id] += 1
            return True

    async def touch(
        self,
        chat_id: int,
        user_id: int,
        now: datetime,
        title: str | None = None,
    ) -> None:
        """Remember membership without counting towards chat activity."""

        async with self._lock:
            self._touch_locked(chat_id, user_id, now, title)

    async def drain(self) -> dict[int, int]:
        async with self._lock:
            drained = dict(self._counts)
            self._counts.clear()
            return drained

    async def drain_touches(self) -> dict[tuple[int, int], MemberTouch]:
        async with self._lock:
            drained = dict(self._touches)
            self._touches.clear()
            return drained

    async def restore(
        self,
        counts: dict[int, int],
        touches: dict[tuple[int, int], MemberTouch] | None = None,
    ) -> None:
        async with self._lock:
            for chat_id, count in counts.items():
                self._counts[chat_id] += count
            for (chat_id, user_id), touch in (touches or {}).items():
                self._touch_locked(chat_id, user_id, touch.last_seen_at, touch.title)
