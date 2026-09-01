"""Pure activity decay and composite trigger policy."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Literal, Protocol

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

    def trigger_reason(
        self,
        score: float,
        recent_message_count: int,
        activity_updated_at: datetime,
        last_event_at: datetime | None,
        now: datetime,
        rng: RandomSource,
    ) -> Literal["peak", "inertia", "random"] | None:
        """Choose one trigger channel, preferring current activity over chance."""
        cooldown = timedelta(seconds=self.settings.activity_event_cooldown_seconds)
        if last_event_at is not None and now < last_event_at + cooldown:
            return None

        if (
            score >= self.settings.activity_threshold
            and recent_message_count >= self.settings.activity_peak_messages
        ):
            return "peak"

        activity_age = max(0.0, (now - activity_updated_at).total_seconds())
        if (
            score >= self.settings.activity_threshold
            and activity_age <= self.settings.activity_inertia_window_seconds
            and rng.randint(1, self.settings.activity_inertia_one_in)
            == self.settings.activity_inertia_one_in
        ):
            return "inertia"

        random_one_in = max(
            1,
            math.ceil(
                self.settings.activity_random_average_seconds
                / self.settings.tick_seconds
            ),
        )
        if rng.randint(1, random_one_in) == random_one_in:
            return "random"
        return None
