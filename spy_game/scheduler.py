"""Pure activity decay and composite trigger policy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from .settings import ACTIVITY_PROFILES, SpySettings


class RandomSource(Protocol):
    def randint(self, start: int, end: int) -> int:
        ...


@dataclass(frozen=True)
class ActivityTriggerSettings:
    profile: str
    threshold: float
    peak_messages: int
    inertia_window_seconds: int
    inertia_one_in: int
    random_average_seconds: int
    event_cooldown_seconds: int


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

    def trigger_settings(self, profile: str | None = None) -> ActivityTriggerSettings:
        selected = profile or self.settings.default_activity_profile
        if selected not in ACTIVITY_PROFILES:
            raise ValueError("unknown activity profile")

        threshold = self.settings.activity_threshold
        peak_messages = self.settings.activity_peak_messages
        inertia_window = self.settings.activity_inertia_window_seconds
        inertia_one_in = self.settings.activity_inertia_one_in
        random_average = self.settings.activity_random_average_seconds
        cooldown = self.settings.activity_event_cooldown_seconds
        if selected == "calm":
            threshold += 0.5
            peak_messages += 1
            inertia_one_in += 1
            random_average = round(random_average * 4 / 3)
            cooldown = round(cooldown * 6 / 5)
        elif selected == "aggressive":
            threshold = max(1.0, threshold - 1.0)
            peak_messages = max(1, peak_messages - 1)
            inertia_window = round(inertia_window * 3 / 2)
            inertia_one_in = max(2, inertia_one_in - 1)
            random_average = max(self.settings.tick_seconds, random_average // 2)
            cooldown = round(cooldown * 4 / 5)
        return ActivityTriggerSettings(
            profile=selected,
            threshold=threshold,
            peak_messages=peak_messages,
            inertia_window_seconds=inertia_window,
            inertia_one_in=inertia_one_in,
            random_average_seconds=random_average,
            event_cooldown_seconds=cooldown,
        )

    def trigger_reason(
        self,
        score: float,
        recent_message_count: int,
        activity_updated_at: datetime,
        last_event_at: datetime | None,
        now: datetime,
        rng: RandomSource,
        profile: str | None = None,
    ) -> Literal["peak", "inertia", "random"] | None:
        """Choose one trigger channel, preferring current activity over chance."""
        trigger = self.trigger_settings(profile)
        cooldown = timedelta(seconds=trigger.event_cooldown_seconds)
        if last_event_at is not None and now < last_event_at + cooldown:
            return None

        if score >= trigger.threshold and recent_message_count >= trigger.peak_messages:
            return "peak"

        activity_age = max(0.0, (now - activity_updated_at).total_seconds())
        if (
            score >= trigger.threshold
            and activity_age <= trigger.inertia_window_seconds
            and rng.randint(1, trigger.inertia_one_in) == trigger.inertia_one_in
        ):
            return "inertia"

        random_one_in = max(
            1,
            math.ceil(trigger.random_average_seconds / self.settings.tick_seconds),
        )
        if rng.randint(1, random_one_in) == random_one_in:
            return "random"
        return None
