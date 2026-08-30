"""Pure activity decay and event-delay policy."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Protocol

from .settings import SpySettings


class RandomSource(Protocol):
    def randint(self, start: int, end: int) -> int:
        ...


class ActivityPolicy:
    def __init__(self, settings: SpySettings) -> None:
        self.settings = settings

    def update_score(
        self,
        score: float,
        updated_at: datetime,
        message_count: int,
        now: datetime,
    ) -> float:
        elapsed = max(0.0, (now - updated_at).total_seconds())
        decay = math.pow(0.5, elapsed / self.settings.activity_half_life_seconds)
        updated = score * decay + message_count * self.settings.activity_message_points
        return min(self.settings.max_activity_score, max(0.0, updated))

    def event_delay_seconds(self, score: float, rng: RandomSource) -> int | None:
        for band in sorted(
            self.settings.activity_bands,
            key=lambda item: item.minimum_score,
            reverse=True,
        ):
            if score >= band.minimum_score:
                return rng.randint(
                    band.minimum_delay_seconds,
                    band.maximum_delay_seconds,
                )
        return None
