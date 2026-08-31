"""Event selection independent from scheduling and Telegram rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .scheduler import RandomSource
from .settings import SpySettings


@dataclass(frozen=True)
class DirectorDecision:
    event_type: str


class GameDirector(Protocol):
    def choose_event(self, previous_event_type: str | None = None) -> DirectorDecision:
        ...


class RuleBasedDirector:
    """Weighted MVP director with a small anti-repeat policy for rare NPC events."""

    def __init__(self, settings: SpySettings, rng: RandomSource) -> None:
        self.settings = settings
        self.rng = rng

    def choose_event(self, previous_event_type: str | None = None) -> DirectorDecision:
        weights = self.settings.event_weights
        if previous_event_type == "handler":
            return DirectorDecision(event_type="recruitment")
        roll = self.rng.randint(1, sum(item.weight for item in weights))
        cumulative = 0
        for item in weights:
            cumulative += item.weight
            if roll <= cumulative:
                return DirectorDecision(event_type=item.event_type)
        raise RuntimeError("event weight selection failed")
